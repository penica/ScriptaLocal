"""Create a new private deployment configuration without overwriting existing state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets


def configure(root,model_root,api_image,worker_image,manifest_sha256,*,port=8090,gpu='nvidia.com/gpu=0',backup_destination=None):
    root=Path(root).resolve();model_root=Path(model_root).resolve()
    if not all(re.fullmatch(r'sha256:[a-f0-9]{64}',value) for value in [api_image,worker_image]):raise ValueError('Provide immutable image IDs')
    manifest=(model_root/'manifest.json').read_bytes()
    if not re.fullmatch(r'[a-f0-9]{64}',manifest_sha256) or hashlib.sha256(manifest).hexdigest()!=manifest_sha256:raise ValueError('Model manifest checksum differs')
    license_name=json.loads(manifest)['license']
    if not isinstance(license_name,str) or not license_name:raise ValueError('Model license metadata is required')
    if type(port)is not int or not 1<=port<=65535 or os.getuid()==0:raise ValueError('Use an unprivileged host user and valid port')
    if not re.fullmatch(r'nvidia\.com/gpu=(?:[0-9]+|GPU-[A-Fa-f0-9-]+)',gpu):raise ValueError('Assign one CDI GPU')
    if not all(re.fullmatch(r'[A-Za-z0-9_./-]+',str(path)) for path in [root,model_root]):raise ValueError('Compose paths require plain absolute path characters')
    root.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in ['secrets','settings','operations','originals','artifacts','status','postgres']:(root/name).mkdir(mode=0o700)
    def write(relative,content,mode=0o600):
        path=root/relative
        with path.open('x') as stream:stream.write(content+'\n')
        path.chmod(mode)
    def document(relative,value):write(relative,json.dumps(value,indent=2))
    app_password=secrets.token_urlsafe(48);admin_password=secrets.token_urlsafe(48)
    caller=secrets.token_urlsafe(48);health=secrets.token_urlsafe(48);manual=secrets.token_urlsafe(32)
    # Parent directories are 0700. The database's separate OS UID must read
    # only these mounted files; API/worker never receive the admin secret.
    write('secrets/admin-password',admin_password,0o444);write('secrets/app-password',app_password,0o444)
    write('secrets/db-dsn','postgresql://scriptalocal_app:'+app_password+'@db:5432/scriptalocal')
    write('secrets/caller.token',caller);write('secrets/health.token',health)
    write('secrets/manual-ui.token',manual)
    document('settings/credentials.json',{'credentials':[
        {'sha256':hashlib.sha256(caller.encode()).hexdigest(),'owner':'registers-integration','capabilities':['read','write','delete']},
        {'sha256':hashlib.sha256(health.encode()).hexdigest(),'owner':'health-probe','capabilities':['read']},
        {'sha256':hashlib.sha256(manual.encode()).hexdigest(),'owner':'manual-submissions','capabilities':['read','write','configure']}]})
    document('settings/api.json',{'dsn_file':'/run/secrets/db_dsn','originals_root':'/data/originals','artifacts_root':'/data/artifacts',
                                 'credentials_file':'/settings/credentials.json','profiles_file':'/status/profiles.json','operator_settings_file':'/operations/operator.json'})
    document('settings/worker.json',{'dsn_file':'/run/secrets/db_dsn','originals_root':'/data/originals','artifacts_root':'/data/artifacts',
                                    'status_root':'/status','model_root':'/model','recognition_manifest_sha256':manifest_sha256,
                                    'runtime_image':worker_image,'worker_id':'gpu-worker-0','precision':'fp32','license':license_name,'scripts':['German Kurrent']})
    document('operations/operator.json',{'backup_destination':str(Path(backup_destination).resolve()) if backup_destination else None})
    values={'SCRIPTALOCAL_SERVICE_ROOT':str(root),'SCRIPTALOCAL_MODEL_ROOT':str(model_root),'SCRIPTALOCAL_API_IMAGE':api_image,
            'SCRIPTALOCAL_WORKER_IMAGE':worker_image,'SCRIPTALOCAL_UID':str(os.getuid()),'SCRIPTALOCAL_GID':str(os.getgid()),
            'SCRIPTALOCAL_API_PORT':str(port),'SCRIPTALOCAL_GPU':gpu}
    write('compose.env','\n'.join(key+"='"+value+"'" for key,value in values.items()))
    return root


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('data/service'));parser.add_argument('--model-root',type=Path,required=True)
    parser.add_argument('--api-image',required=True);parser.add_argument('--worker-image',required=True);parser.add_argument('--model-manifest-sha256',required=True)
    parser.add_argument('--port',type=int,default=8090);parser.add_argument('--gpu',default='nvidia.com/gpu=0');parser.add_argument('--backup-destination',type=Path)
    args=parser.parse_args()
    configure(args.root,args.model_root,args.api_image,args.worker_image,args.model_manifest_sha256,port=args.port,gpu=args.gpu,backup_destination=args.backup_destination)
    print('Private deployment files created. Existing paths are never overwritten; credentials omitted.')


if __name__=='__main__':main()
