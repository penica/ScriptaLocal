"""Validate a received synthetic fixture ZIP without extracting or executing it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path,PurePosixPath
import stat
import zipfile

from pydantic import ValidationError
from scriptalocal import contracts
from scriptalocal.images import decode_image


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--warning-patch',type=Path)
    args=parser.parse_args();blob=args.archive.read_bytes()
    patch=json.loads(args.warning_patch.read_text())['fixtures'] if args.warning_patch else {}
    if len(blob)>10*1024*1024:raise ValueError('Archive exceeds limit')
    with zipfile.ZipFile(args.archive) as archive:
        entries=archive.infolist()
        if len(entries)>200 or sum(e.file_size for e in entries)>10*1024*1024:raise ValueError('Archive expansion exceeds limits')
        for entry in entries:
            path=PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or stat.S_IFMT(entry.external_attr>>16)==stat.S_IFLNK:
                raise ValueError('Unsafe archive member')
        if len(set(archive.namelist()))!=len(entries):raise ValueError('Duplicate archive members')
        manifest=json.loads(archive.read('BUNDLE-MANIFEST.json'))
        listed={item['path']:item for item in manifest['files']}
        if len(listed)!=len(manifest['files']) or set(listed)!=set(archive.namelist())-{'BUNDLE-MANIFEST.json'}:raise ValueError('Manifest member mismatch')
        for name,item in listed.items():
            data=archive.read(name)
            if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Manifest checksum mismatch')
        root='tests/fixtures/scriptalocal/'
        mapping=json.loads(archive.read(root+'mock-schema-map.json'))
        allowed={key:getattr(contracts,key) for key in ['JobAccepted','JobStatus','JobOptions','JobResult','ErrorResponse']}
        results=[]
        for name,model in mapping.items():
            if model not in allowed or '/' in name:raise ValueError('Unexpected fixture mapping')
            try:
                content=archive.read(root+name)
                if name in patch:
                    obj=json.loads(content)
                    for operation in patch[name]:
                        if operation['op']!='add' or not operation['path'].endswith('/warnings'):raise ValueError('Only explicit warning additions are supported')
                        keys=operation['path'].strip('/').split('/');target=obj
                        for key in keys[:-1]:target=target[int(key)] if isinstance(target,list) else target[key]
                        target[keys[-1]]=operation['value']
                    content=json.dumps(obj).encode()
                allowed[model].model_validate_json(content)
                results.append({'file':name,'model':model,'valid':True})
            except ValidationError as exc:
                results.append({'file':name,'model':model,'valid':False,'errors':[{'location':list(e['loc']),'message':e['msg']} for e in exc.errors(include_input=False)]})
        negatives=[]
        for mutation in ['order','blank','bounds','null_flattened']:
            result=json.loads(archive.read(root+('canonical-result-partial.json' if mutation=='null_flattened' else 'canonical-result.json')))
            if mutation=='order':result['cells'][1]['order']=0
            elif mutation=='blank':result['cells'][1]['text']='NOT BLANK'
            elif mutation=='bounds':result['cells'][0]['polygon'][0][0]=301
            else:result['cells'][0]['text']='SYNTHETIC LINE A'
            try:contracts.JobResult.model_validate_json(json.dumps(result))
            except ValidationError:negatives.append({'mutation':mutation,'rejected':True})
            else:negatives.append({'mutation':mutation,'rejected':False})
        png=archive.read(root+'three-cells.png');decoded=decode_image(png,declared_mime='image/png')
        request=json.loads(archive.read(root+'current-request.json'))
        assert base64.b64decode(request['png_base64'],validate=True)==png
        for name,model in mapping.items():
            if model=='JobResult':
                result=json.loads(archive.read(root+name))
                assert result['source']['sha256']==decoded.sha256
                assert (result['source']['width'],result['source']['height'])==decoded.encoded_size
        spec=archive.read('references/scriptalocal/2026-11-12/scriptalocal-openapi.json')
        current=Path('docs/api/openapi.json').read_bytes()
        report={'proposed_warning_patch_applied_in_memory':bool(patch),'archive_bytes':len(blob),'archive_sha256':hashlib.sha256(blob).hexdigest(),'archive_members':len(entries),'manifest_entries_verified':len(listed),'canonical_fixtures':results,'negative_semantics':negatives,'image':{'width':decoded.encoded_size[0],'height':decoded.encoded_size[1],'sha256':decoded.sha256,'app_base64_matches':True},'supplied_openapi_matches_current':spec==current,'scope':'Offline service-model validation only; no HTTP, app adapter, database or accuracy acceptance'}
        print(json.dumps(report,indent=2))
        if not all(item['valid'] for item in results) or not all(item['rejected'] for item in negatives):raise SystemExit(1)

if __name__=='__main__':main()
