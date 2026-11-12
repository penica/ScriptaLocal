"""File-backed caller capabilities and verified resident-worker profiles."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from .contracts import ModelReference
from .job_queue import canonical_hash,principal


class ServiceError(ValueError):
    def __init__(self,code,message,status=422):super().__init__(message);self.code=code;self.status=status


def private_json(path,maximum_bytes=1024*1024):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>maximum_bytes:raise ValueError('Invalid configuration file')
        content=stream.read(maximum_bytes+1)
        if len(content)>maximum_bytes:raise ValueError('Configuration exceeds limit')
    return json.loads(content)


@dataclass(frozen=True)
class Caller:
    owner:str
    capabilities:frozenset[str]

    def require(self,capability):
        if capability not in self.capabilities:raise ServiceError('forbidden','Required capability is unavailable',403)


class CredentialStore:
    def __init__(self,path):self.path=Path(path)

    def authenticate(self,authorization):
        scheme,separator,token=authorization.partition(' ')
        if not separator or scheme.lower()!='bearer' or not 32<=len(token)<=256:
            raise ServiceError('unauthorized','Bearer credentials required',401)
        try:
            records=private_json(self.path,65536)['credentials']
            if not isinstance(records,list) or len(records)>128:raise ValueError
            found=None;seen=set();supplied=hashlib.sha256(token.encode()).hexdigest()
            for record in records:
                if not re.fullmatch(r'[a-f0-9]{64}',record['sha256']) or record['sha256'] in seen:raise ValueError
                seen.add(record['sha256'])
                owner=principal(record['owner']);capabilities=record['capabilities']
                if not isinstance(capabilities,list) or not capabilities or set(capabilities)-{'read','write','delete'}:raise ValueError
                enabled=record.get('enabled',True)
                if type(enabled)is not bool:raise ValueError
                expires=record.get('expires_at')
                if expires is not None and (not isinstance(expires,(int,float)) or isinstance(expires,bool) or not math.isfinite(expires)):raise ValueError
                if hmac.compare_digest(supplied,record['sha256']) and enabled and (expires is None or expires>time.time()):
                    found=Caller(owner,frozenset(capabilities))
            if found is not None:return found
        except (OSError,ValueError,KeyError,TypeError):raise ServiceError('credentials_unavailable','Credential configuration is unavailable',503) from None
        raise ServiceError('unauthorized','Bearer credentials required',401)


@dataclass(frozen=True)
class WorkerProfile:
    model_id:str
    configuration_sha256:str
    reference:dict
    license:str
    scripts:list
    readiness:str


class ProfileRegistry:
    def __init__(self,path,*,maximum_ready_age_seconds=60):
        self.path=Path(path)
        if isinstance(maximum_ready_age_seconds,bool) or not isinstance(maximum_ready_age_seconds,(int,float)) or not math.isfinite(maximum_ready_age_seconds) or maximum_ready_age_seconds<=0:
            raise ValueError('Readiness age must be finite and positive')
        self.maximum_ready_age_seconds=maximum_ready_age_seconds

    def snapshot(self):
        try:
            data=private_json(self.path);profiles=[];seen=set()
            for item in data['profiles']:
                manifest=item['configuration'];checksum=canonical_hash(manifest)
                reference=ModelReference.model_validate(manifest['models']['recognition']).model_dump(mode='json')
                model_id=reference['model_id']
                if model_id in seen:raise ValueError('Duplicate model')
                seen.add(model_id)
                if not isinstance(item['license'],str) or not item['license'] or not isinstance(item['scripts'],list) or not all(isinstance(script,str) and script for script in item['scripts']):raise ValueError
                enabled=item.get('enabled',True)
                if type(enabled)is not bool:raise ValueError
                readiness='unavailable'
                if enabled:
                    try:
                        marker=private_json(Path(item['ready_file']),65536)
                        elapsed=time.time()-marker['last_seen']
                        if 0<=elapsed<=self.maximum_ready_age_seconds and marker['configuration_sha256']==checksum and marker['state'] in {'ready','warming'}:
                            readiness=marker['state']
                    except (OSError,ValueError,KeyError,TypeError):pass
                profiles.append(WorkerProfile(model_id,checksum,reference,item['license'],item['scripts'],readiness))
            if not profiles or data['default_model'] not in seen:raise ValueError
            return data['default_model'],profiles
        except (OSError,ValueError,KeyError,TypeError):raise ServiceError('profiles_unavailable','Model configuration is unavailable',503) from None

    def select(self,model_id):
        default,profiles=self.snapshot();selected=model_id or default
        profile=next((profile for profile in profiles if profile.model_id==selected),None)
        if profile is None:raise ServiceError('model_unavailable','Requested model is unavailable',503)
        if profile.readiness!='ready':raise ServiceError('worker_unavailable','The selected worker is not ready',503)
        return profile
