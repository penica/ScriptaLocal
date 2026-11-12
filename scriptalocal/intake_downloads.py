"""Explicitly published immutable snapshots, with separate download capabilities."""
import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import time
from uuid import uuid4

from .intake_store import IntakeError, clean_filename, clean_subfolder, open_destination


def initialize(store):
    with store.connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS downloads(
          id TEXT PRIMARY KEY,token_hash TEXT NOT NULL,label TEXT NOT NULL,
          expires REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,manifest TEXT NOT NULL)''')
        db.commit()
    (store.state/'downloads').mkdir(mode=0o700,exist_ok=True)


def publish(store,options):
    settings=store.settings
    root=settings.destinations.get(options.destination)
    if root is None:raise IntakeError('unknown_destination','Unknown destination alias')
    subfolder=clean_subfolder(options.subfolder)
    names=[clean_filename(name) for name in options.filenames]
    if len(set(names))!=len(names):raise IntakeError('invalid_request','Duplicate filenames')
    if len(names)>settings.maximum_files or options.expires_in_seconds>settings.maximum_expiry_seconds:
        raise IntakeError('request_limit_exceeded','Download request exceeds configured limits')
    identifier=str(uuid4());token=secrets.token_urlsafe(32)
    folder=store.state/'downloads'/identifier
    folder.mkdir(mode=0o700)
    manifest=[];total=0
    directory=None
    try:
        directory=open_destination(root,subfolder)
        for name in names:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>settings.maximum_file_bytes:
                    raise IntakeError('invalid_file','Only bounded regular files can be published')
                content=stream.read(settings.maximum_file_bytes+1)
            total+=len(content)
            if len(content)>settings.maximum_file_bytes or total>settings.maximum_total_bytes:
                raise IntakeError('request_limit_exceeded','Download bytes exceed configured limits')
            file_id=str(uuid4());target=folder/file_id
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as output:
                output.write(content);output.flush();os.fsync(output.fileno())
            manifest.append({'file_id':file_id,'filename':name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
        for path in [folder,folder.parent]:
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        with store.transaction() as db:
            db.execute('INSERT INTO downloads(id,token_hash,label,expires,manifest) VALUES(?,?,?,?,?)',
                       (identifier,hashlib.sha256(token.encode()).hexdigest(),options.label,
                        time.time()+options.expires_in_seconds,json.dumps(manifest)))
        return describe(store,identifier,admin=True),token
    except BaseException:
        shutil.rmtree(folder)
        raise
    finally:
        if directory is not None:os.close(directory)


def authorized(store,identifier,token='',admin=False):
    with store.connection() as db:row=db.execute('SELECT * FROM downloads WHERE id=?',(identifier,)).fetchone()
    if row is None or (not admin and not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest())):
        raise IntakeError('not_found','Download request not found',404)
    if not admin and (row['revoked'] or row['expires']<=time.time()):
        raise IntakeError('request_closed','Download request is expired or revoked',410)
    return row


def describe(store,identifier,token='',admin=False):
    row=authorized(store,identifier,token,admin)
    return {'request_id':identifier,'label':row['label'],'expires_at':row['expires'],
            'revoked':bool(row['revoked']),'files':json.loads(row['manifest'])}


def read(store,identifier,file_id,token):
    row=authorized(store,identifier,token)
    record=next((item for item in json.loads(row['manifest']) if item['file_id']==file_id),None)
    if record is None:raise IntakeError('not_found','Download file not found',404)
    directory=open_destination(store.state/'downloads',identifier)
    try:
        fd=os.open(file_id,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size!=record['bytes']:
                raise IntakeError('artifact_changed','Download snapshot changed',409)
            content=stream.read(store.settings.maximum_file_bytes+1)
    finally:os.close(directory)
    if len(content)!=record['bytes'] or hashlib.sha256(content).hexdigest()!=record['sha256']:
        raise IntakeError('artifact_changed','Download snapshot changed',409)
    authorized(store,identifier,token)
    return record,content


def revoke(store,identifier):
    authorized(store,identifier,admin=True)
    with store.transaction() as db:db.execute('UPDATE downloads SET revoked=1 WHERE id=?',(identifier,))
    return describe(store,identifier,admin=True)
