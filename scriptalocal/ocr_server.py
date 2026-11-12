"""Explicit file-configured ASGI factory; schema changes remain an operator action."""
import os
from pathlib import Path
import stat

from .images import ImageLimits
from .job_artifacts import ArtifactStore
from .job_queue import JobQueue,QueueSettings
from .ocr_access import CredentialStore,ProfileRegistry,private_json
from .ocr_api import APISettings,create_app
from .source_store import SourceStore


def absolute_path(value):
    if not isinstance(value,str) or not Path(value).is_absolute():raise ValueError('Configuration paths must be absolute')
    return Path(value)


def read_dsn(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>16384:raise ValueError('Invalid database secret file')
        content=stream.read(16385)
    if len(content)>16384:raise ValueError('Database secret exceeds limit')
    value=content.decode().strip()
    if not value:raise ValueError('Database secret is empty')
    return value


def build_app(config_path):
    data=private_json(absolute_path(str(config_path)),65536)
    required={'dsn_file','originals_root','artifacts_root','credentials_file','profiles_file'}
    allowed=required|{'api','queue','maximum_artifact_bytes','maximum_ready_age_seconds','operator_settings_file'}
    if not isinstance(data,dict) or not required<=data.keys() or data.keys()-allowed:
        raise ValueError('Invalid server configuration keys')
    paths={name:absolute_path(data[name]) for name in required}
    api=dict(data.get('api',{}))
    api['image']=ImageLimits(**api.get('image',{}))
    settings=APISettings(**api)
    sources=SourceStore(paths['originals_root'])
    artifacts=ArtifactStore(paths['artifacts_root'],maximum_bytes=data.get('maximum_artifact_bytes',64*1024*1024))
    queue=JobQueue(read_dsn(paths['dsn_file']),artifacts,settings=QueueSettings(**data.get('queue',{})))
    credentials=CredentialStore(paths['credentials_file'])
    profiles=ProfileRegistry(paths['profiles_file'],maximum_ready_age_seconds=data.get('maximum_ready_age_seconds',60))
    # Readiness reports missing schema/model readiness. Startup does not migrate,
    # provision models, or assume a worker exists merely because files exist.
    operator=absolute_path(data['operator_settings_file']) if 'operator_settings_file' in data else None
    return create_app(queue,sources,credentials,profiles,settings=settings,operator_path=operator)


def application():
    try:return build_app(os.environ['SCRIPTALOCAL_CONFIG'])
    except Exception:
        # Uvicorn logs factory errors; never attach secret paths or DSNs.
        raise RuntimeError('OCR server configuration is unavailable') from None
