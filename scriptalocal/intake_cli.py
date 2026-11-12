"""Bootstrap intake and issue private upload handovers; uploader needs no shell access."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener



class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def private_write(path,content):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())


def call(url,*,method='GET',token,document=None,data=None,headers=None):
    fields={'Authorization':'Bearer '+token,**(headers or {})}
    if document is not None:
        data=json.dumps(document).encode();fields['Content-Type']='application/json'
    request=Request(url,data=data,method=method,headers=fields)
    try:
        with build_opener(NoRedirect()).open(request,timeout=150) as response:return json.load(response)
    except HTTPError as exc:
        try:detail=json.load(exc)['error']['message']
        except Exception:detail='Request failed'
        raise SystemExit(f'HTTP {exc.code}: {detail}') from None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=Path(os.environ.get('SCRIPTALOCAL_INTAKE_CONFIG','data/intake/config.json')))
    parser.add_argument('--api-url',default=os.environ.get('SCRIPTALOCAL_INTAKE_ADMIN_URL','http://127.0.0.1:8091'))
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('init');init.add_argument('--data-root',type=Path,required=True)
    init.add_argument('--public-base-url',default='http://127.0.0.1:8091')
    create=commands.add_parser('request');create.add_argument('--destination',required=True)
    create.add_argument('--subfolder',default='');create.add_argument('--label',required=True)
    create.add_argument('--max-files',type=int,default=2);create.add_argument('--max-file-bytes',type=int,default=10*1024*1024)
    create.add_argument('--max-total-bytes',type=int,default=20*1024*1024)
    create.add_argument('--expires-in-seconds',type=int,default=86400)
    create.add_argument('--allowed-suffixes',default='.md,.json,.yaml,.yml,.txt')
    create.add_argument('--output-directory',type=Path)
    upload=commands.add_parser('upload');upload.add_argument('--ticket',type=Path,required=True)
    upload.add_argument('files',type=Path,nargs='+')
    share=commands.add_parser('share');share.add_argument('--destination',required=True)
    share.add_argument('--subfolder',default='');share.add_argument('--label',required=True)
    share.add_argument('--expires-in-seconds',type=int,default=86400)
    share.add_argument('--output-directory',type=Path)
    share.add_argument('filenames',nargs='+')
    unshare=commands.add_parser('unshare');unshare.add_argument('request_id')
    status=commands.add_parser('status');status.add_argument('request_id')
    revoke=commands.add_parser('revoke');revoke.add_argument('request_id')
    args=parser.parse_args()
    if args.command!='upload':
        from .intake_store import IntakeSettings
    if args.command=='init':
        root=args.data_root.resolve();private=root/'intake'
        for path in [private/'state',private/'requests',private/'uploads',root/'integration-handover',
                     root/'reference/row-crops',root/'reference/ground-truth']:
            path.mkdir(parents=True,exist_ok=True,mode=0o700)
        token_path=private/'admin.token'
        if args.config.exists() or token_path.exists():raise SystemExit('Configuration or administrator credential already exists; no files changed')
        token=secrets.token_urlsafe(32)
        config={'state_root':str(private/'state'),'admin_token_file':str(token_path),
                'public_base_url':args.public_base_url,'destinations':{
                    'integration-handover':str(root/'integration-handover'),'row-crops':str(root/'reference/row-crops'),
                    'ground-truth':str(root/'reference/ground-truth'),'inbox':str(private/'uploads')}}
        # Validate before publishing credentials or configuration.
        IntakeSettings(state_root=Path(config['state_root']),admin_token=token,
                       public_base_url=config['public_base_url'],destinations={k:Path(v) for k,v in config['destinations'].items()})
        private_write(token_path,token+'\n');private_write(args.config,json.dumps(config,indent=2)+'\n')
        print(json.dumps({'config':str(args.config),'administrator_credential':str(token_path),'token_printed':False}))
        return
    if args.command=='upload':
        ticket=json.loads(args.ticket.read_text())
        for path in args.files:
            with path.open('rb') as stream:
                receipt=call(ticket['upload_url'],method='PUT',token=ticket['upload_token'],data=stream,
                    headers={'X-Upload-Filename':quote(path.name,safe=''),'Content-Type':'application/octet-stream',
                             'Content-Length':str(path.stat().st_size)})
            print(json.dumps(receipt))
        return
    settings=IntakeSettings.load(args.config)
    if args.command=='unshare':
        result=call(args.api_url.rstrip('/')+'/intake/v1/downloads/'+args.request_id+'/revoke',method='POST',token=settings.admin_token)
        print(json.dumps(result,indent=2));return
    if args.command=='share':
        options={name:getattr(args,name) for name in ['destination','subfolder','label','filenames','expires_in_seconds']}
        ticket=call(args.api_url.rstrip('/')+'/intake/v1/downloads',method='POST',token=settings.admin_token,document=options)
        directory=args.output_directory or settings.state_root.parent/'requests'
        ticket_path=directory/(ticket['request_id']+'-download.json')
        handover_path=directory/(ticket['request_id']+'-download.md')
        private_write(ticket_path,json.dumps(ticket,indent=2)+'\n')
        text='''# Agent download handover

Download the explicitly shared files below. Use the bearer token only with these URLs. Do not follow redirects or commit the credential. Verify each downloaded SHA-256 before reading it.

'''
        text+='Manifest URL: '+ticket['manifest_url']+'\n\n'
        text+='Authorization: Bearer '+ticket['download_token']+'\n\n'
        text+='Expiry (UTC): '+datetime.fromtimestamp(ticket['expires_at'],timezone.utc).isoformat()+'\n\n'
        for item in ticket['files']:
            text+='- '+item['filename']+' ('+str(item['bytes'])+' bytes)\n'
            text+='  - GET '+ticket['manifest_url']+'/files/'+item['file_id']+'\n'
            text+='  - SHA-256: '+item['sha256']+'\n'
        text+='\nThis credential grants read-only access to these snapshots. It cannot upload or administer the service.\n'
        private_write(handover_path,text)
        print(json.dumps({'request_id':ticket['request_id'],'ticket_file':str(ticket_path),'handover_file':str(handover_path),'token_printed':False}));return
    base=args.api_url.rstrip('/')+'/intake/v1/requests'
    if args.command in {'status','revoke'}:
        url=base+'/'+args.request_id+('/revoke' if args.command=='revoke' else '')
        result=call(url,method='POST' if args.command=='revoke' else 'GET',token=settings.admin_token)
        print(json.dumps(result,indent=2));return
    options={name:getattr(args,name) for name in ['destination','subfolder','label','max_files','max_file_bytes','max_total_bytes','expires_in_seconds']}
    options['allowed_suffixes']=args.allowed_suffixes.split(',')
    ticket=call(base,method='POST',token=settings.admin_token,document=options)
    directory=args.output_directory or settings.state_root.parent/'requests'
    ticket_path=directory/(ticket['request_id']+'.json')
    handover_path=directory/(ticket['request_id']+'.md')
    private_write(ticket_path,json.dumps(ticket,indent=2)+'\n')
    expires=datetime.fromtimestamp(ticket['expires_at'],timezone.utc).isoformat()
    text=f'''# Agent upload request\n\nUpload the requested files to ScriptaLocal using this scoped ticket.\n\n- Destination: `{ticket['destination']}/{ticket['subfolder']}`\n- Expiry (UTC): {expires}\n- Maximum files: {ticket['max_files']}\n- Maximum bytes per file: {ticket['max_file_bytes']}\n- Allowed suffixes: {', '.join(ticket['allowed_suffixes'])}\n\nSend one raw file per PUT request to `{ticket['upload_url']}`.\nUse header `Authorization: Bearer {ticket['upload_token']}` and `X-Upload-Filename` containing the percent-encoded UTF-8 basename, without folders. Set Content-Type to application/octet-stream and send the file bytes unchanged.\n\nSuccessful uploads return a SHA-256 receipt. Repeating the same filename/content is safe. Different content never overwrites a destination file. This token grants upload/status access only to this request; do not commit it.\n\nWith the ScriptaLocal checkout and Python, save the following JSON privately as upload-ticket.json, then run:\n\n```bash\npython -m scriptalocal.intake_cli upload --ticket upload-ticket.json /path/to/first-file /path/to/second-file\n```\n\n```json\n{json.dumps(ticket,indent=2)}\n```\n'''
    private_write(handover_path,text)
    print(json.dumps({'request_id':ticket['request_id'],'ticket_file':str(ticket_path),
                      'handover_file':str(handover_path),'expires_at':expires,'token_printed':False}))


if __name__=='__main__':main()
