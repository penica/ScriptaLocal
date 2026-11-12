"""Durable, folder-scoped upload tickets; never stores raw upload credentials."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
import unicodedata
from urllib.parse import urlsplit
from uuid import uuid4


class IntakeError(ValueError):
    def __init__(self,code,message,status=422):
        super().__init__(message);self.code=code;self.status=status


@dataclass(frozen=True)
class IntakeSettings:
    state_root:Path
    destinations:dict[str,Path]
    admin_token:str=field(repr=False)
    public_base_url:str='http://127.0.0.1:8091'
    maximum_files:int=50
    maximum_file_bytes:int=25*1024*1024
    maximum_total_bytes:int=250*1024*1024
    maximum_expiry_seconds:int=7*86400
    upload_timeout_seconds:int=120
    maximum_concurrent_uploads:int=4

    def __post_init__(self):
        if len(self.admin_token)<32:raise ValueError('Use a random administrator token of at least 32 characters')
        url=urlsplit(self.public_base_url)
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Invalid public upload base URL')
        if not self.destinations:raise ValueError('Configure at least one upload destination')
        for value in [self.maximum_files,self.maximum_file_bytes,self.maximum_total_bytes,
                      self.maximum_expiry_seconds,self.upload_timeout_seconds,self.maximum_concurrent_uploads]:
            if type(value)is not int or value<=0:raise ValueError('Upload limits must be positive integers')
        for key,path in self.destinations.items():
            if not key or not all(c.isalnum() or c in '-_' for c in key):raise ValueError('Invalid destination alias')
            root=Path(path).resolve();state=self.state_root.resolve()
            if root==state or root in state.parents or state in root.parents:
                raise ValueError('Upload destinations must not overlap ticket state storage')

    @classmethod
    def load(cls,path):
        data=json.loads(Path(path).read_text())
        token=Path(data.pop('admin_token_file')).read_text().strip()
        data['state_root']=Path(data['state_root'])
        data['destinations']={name:Path(root) for name,root in data['destinations'].items()}
        return cls(admin_token=token,**data)


def clean_subfolder(value):
    if not isinstance(value,str) or len(value)>500 or '\\' in value or '\x00' in value:
        raise IntakeError('invalid_destination','Invalid destination subfolder')
    if not value:return ''
    parts=value.split('/')
    if any(part in {'','.','..'} or part.startswith('.') or len(part)>100 or
           not all(c.isalnum() or c in '-_' for c in part) for part in parts):
        raise IntakeError('invalid_destination','Use relative alphanumeric subfolder components')
    return '/'.join(parts)


def clean_filename(value):
    value=unicodedata.normalize('NFC',value)
    if not value or len(value)>200 or value.startswith('.') or value in {'.','..'} or any(
            not(c.isalnum() or c in ' ._()-') for c in value):
        raise IntakeError('invalid_filename','Use a plain filename without paths or control characters')
    if value.endswith((' ','.')):raise IntakeError('invalid_filename','Filename cannot end in a space or dot')
    return value


def open_destination(root,subfolder):
    root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in clean_subfolder(subfolder).split('/') if subfolder else []:
            try:
                os.mkdir(part,0o700,dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:pass
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        return fd
    except BaseException:
        os.close(fd);raise


class IntakeStore:
    def __init__(self,settings):
        self.settings=settings;self.state=settings.state_root.resolve()
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock_fd=os.open(self.state/'service.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:os.close(self.lock_fd);raise RuntimeError('An intake process already uses this state directory')
        self.database=self.state/'tickets.sqlite3'
        fd=os.open(self.database,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):os.close(fd);self.close();raise ValueError('State database must be a regular file')
        os.close(fd)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS requests(
              id TEXT PRIMARY KEY,token_hash TEXT NOT NULL,destination TEXT NOT NULL,
              root TEXT NOT NULL,subfolder TEXT NOT NULL,label TEXT NOT NULL,
              created REAL NOT NULL,expires REAL NOT NULL,max_files INTEGER NOT NULL,
              max_file_bytes INTEGER NOT NULL,max_total_bytes INTEGER NOT NULL,
              suffixes TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS files(
              id TEXT PRIMARY KEY,request_id TEXT NOT NULL,filename TEXT NOT NULL,
              status TEXT NOT NULL,size INTEGER,sha256 TEXT,
              UNIQUE(request_id,filename),FOREIGN KEY(request_id) REFERENCES requests(id));
            ''')
        # The process lock proves no other writer is active during recovery.
        with self.connection() as db:
            pending=db.execute('SELECT files.id,requests.* FROM files JOIN requests ON requests.id=files.request_id ').fetchall()
        for row in pending:
            try:
                root=self.destination_root(row)
                fd=open_destination(root,row['subfolder'])
                try:
                    try:os.unlink('.intake-'+row[0]+'.part',dir_fd=fd)
                    except FileNotFoundError:pass
                finally:os.close(fd)
            except (IntakeError,OSError):pass
        with self.transaction() as db:db.execute('DELETE FROM files WHERE status="staging"')

    def close(self):
        if getattr(self,'lock_fd',None)is not None:
            os.close(self.lock_fd);self.lock_fd=None

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.database,timeout=10)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:yield db
        finally:db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def destination_root(self,row):
        configured=self.settings.destinations.get(row['destination'])
        if configured is None or str(Path(configured).resolve())!=row['root']:
            raise IntakeError('destination_changed','Destination configuration changed; request a new ticket',409)
        return Path(row['root'])

    def request(self,identifier,token=None,admin=False):
        with self.connection() as db:row=db.execute('SELECT * FROM requests WHERE id=?',(identifier,)).fetchone()
        supplied=hashlib.sha256((token or '').encode()).hexdigest()
        if row is None or (not admin and not hmac.compare_digest(row['token_hash'],supplied)):
            raise IntakeError('not_found','Upload request not found',404)
        if not admin and (row['revoked'] or row['expires']<=time.time()):
            raise IntakeError('request_closed','Upload request is expired or revoked',410)
        if not admin:self.destination_root(row)
        return row

    def create(self,options):
        destination=options['destination'];subfolder=clean_subfolder(options.get('subfolder',''))
        root=self.settings.destinations.get(destination)
        if root is None:raise IntakeError('unknown_destination','Unknown destination alias')
        root=Path(root).resolve()
        fd=open_destination(root,subfolder);os.close(fd)
        identifier=str(uuid4());token=secrets.token_urlsafe(32);now=time.time()
        with self.transaction() as db:
            db.execute('INSERT INTO requests(id,token_hash,destination,root,subfolder,label,created,expires,max_files,max_file_bytes,max_total_bytes,suffixes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
                identifier,hashlib.sha256(token.encode()).hexdigest(),destination,str(root),subfolder,
                options['label'],now,now+options['expires_in_seconds'],options['max_files'],
                options['max_file_bytes'],options['max_total_bytes'],json.dumps(options['allowed_suffixes'])))
        return self.describe(identifier),token

    def describe(self,identifier):
        row=self.request(identifier,admin=True)
        with self.connection() as db:
            files=[dict(item) for item in db.execute('SELECT id,filename,size,sha256 FROM files WHERE request_id=? AND status="complete" ORDER BY rowid',(identifier,))]
            staging=db.execute('SELECT count(*) FROM files WHERE request_id=? AND status="staging"',(identifier,)).fetchone()[0]
        return {'request_id':identifier,'destination':row['destination'],'subfolder':row['subfolder'],
                'label':row['label'],'expires_at':row['expires'],'revoked':bool(row['revoked']),
                'max_files':row['max_files'],'max_file_bytes':row['max_file_bytes'],
                'max_total_bytes':row['max_total_bytes'],'allowed_suffixes':json.loads(row['suffixes']),
                'files':files,'remaining_files':row['max_files']-len(files)-staging}

    def reserve(self,row,filename):
        if Path(filename).suffix.lower() not in json.loads(row['suffixes']):
            raise IntakeError('file_type_not_allowed','Filename extension is not allowed by this upload request',415)
        with self.transaction() as db:
            current=db.execute('SELECT * FROM requests WHERE id=?',(row['id'],)).fetchone()
            self.check_open(current)
            existing=db.execute('SELECT * FROM files WHERE request_id=? AND filename=?',(row['id'],filename)).fetchone()
            if existing:
                if existing['status']=='staging':raise IntakeError('upload_in_progress','This filename is already being uploaded',409)
                return dict(existing),True
            count=db.execute('SELECT count(*) FROM files WHERE request_id=?',(row['id'],)).fetchone()[0]
            if count>=current['max_files']:raise IntakeError('file_limit_reached','Upload request has no remaining file slots',409)
            identifier=str(uuid4())
            db.execute('INSERT INTO files(id,request_id,filename,status) VALUES(?,?,?,"staging")',(identifier,row['id'],filename))
        return {'id':identifier,'request_id':row['id'],'filename':filename},False

    def check_open(self,row):
        if row['revoked'] or row['expires']<=time.time():raise IntakeError('request_closed','Upload request is expired or revoked',410)

    def abort(self,reservation):
        with self.transaction() as db:db.execute('DELETE FROM files WHERE id=? AND status="staging"',(reservation['id'],))

    def finalize(self,row,reservation,size,digest,directory_fd,temporary,replay):
        with self.transaction() as db:
            current=db.execute('SELECT * FROM requests WHERE id=?',(row['id'],)).fetchone();self.check_open(current)
            record=db.execute('SELECT * FROM files WHERE id=?',(reservation['id'],)).fetchone()
            if record is None:raise IntakeError('upload_reservation_lost','Upload reservation is no longer active',409)
            if replay:
                if record['size']!=size or record['sha256']!=digest:
                    raise IntakeError('filename_conflict','This filename already has different content',409)
                try:
                    fd=os.open(reservation['filename'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory_fd)
                    with os.fdopen(fd,'rb') as stream:
                        info=os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size!=size or hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
                            raise IntakeError('artifact_changed','Uploaded artifact no longer matches its receipt',409)
                except OSError as exc:raise IntakeError('artifact_changed','Uploaded artifact is no longer available',409) from exc
                return
            used=db.execute('SELECT coalesce(sum(size),0) FROM files WHERE request_id=? AND status="complete"',(row['id'],)).fetchone()[0]
            if used+size>current['max_total_bytes']:raise IntakeError('total_bytes_exceeded','Upload request total byte limit exceeded',413)
            try:os.link(temporary,reservation['filename'],src_dir_fd=directory_fd,dst_dir_fd=directory_fd,follow_symlinks=False)
            except FileExistsError:
                try:
                    fd=os.open(reservation['filename'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory_fd)
                    with os.fdopen(fd,'rb') as stream:
                        info=os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size!=size or hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
                            raise IntakeError('filename_conflict','Destination filename already has different content',409)
                except OSError as exc:raise IntakeError('filename_conflict','Destination filename is not available',409) from exc
            os.fsync(directory_fd)
            db.execute('UPDATE files SET status="complete",size=?,sha256=? WHERE id=?',(size,digest,reservation['id']))

    def revoke(self,identifier):
        self.request(identifier,admin=True)
        with self.transaction() as db:db.execute('UPDATE requests SET revoked=1 WHERE id=?',(identifier,))
