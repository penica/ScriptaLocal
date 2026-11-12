"""Shared-original locks coordinate publication and explicit source collection."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat

from .images import persist_original
from .ocr_access import ServiceError


class SourceStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        (self.root/'.locks').mkdir(mode=0o700,exist_ok=True)

    @contextmanager
    def locked(self,digest):
        if not re.fullmatch(r'[a-f0-9]{64}',digest):raise ValueError('Invalid source hash')
        root=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:locks=os.open('.locks',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        finally:os.close(root)
        try:fd=os.open(digest,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=locks)
        finally:os.close(locks)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Source lock must be a regular file')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ServiceError('source_busy','Source operation is in progress; retry with the same key',429) from None
            yield
        finally:os.close(fd)

    def publish_locked(self,image):
        path=self.root/(image.sha256+'.original')
        try:info=path.lstat()
        except FileNotFoundError:pass
        else:
            if not stat.S_ISREG(info.st_mode):raise ValueError('Existing source must be a regular file')
        return persist_original(image,self.root)

    @contextmanager
    def _stream(self,digest,maximum_bytes):
        if not re.fullmatch(r'[a-f0-9]{64}',digest) or type(maximum_bytes)is not int or maximum_bytes<1:raise ValueError('Invalid source read limits')
        try:
            root=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:fd=os.open(digest+'.original',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
            finally:os.close(root)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>maximum_bytes:raise ValueError('Invalid source file')
                yield stream
        except (OSError,ValueError):raise ServiceError('source_unavailable','Original image is unavailable',503) from None

    def size(self,digest,maximum_bytes):
        with self._stream(digest,maximum_bytes) as stream:return os.fstat(stream.fileno()).st_size

    def read(self,digest,maximum_bytes):
        with self._stream(digest,maximum_bytes) as stream:
            content=stream.read(maximum_bytes+1)
            if len(content)>maximum_bytes or hashlib.sha256(content).hexdigest()!=digest:raise ValueError('Source checksum differs')
            return content

    def remove_locked(self,digest):
        # Caller holds the hash lock and has checked no live metadata references.
        if not re.fullmatch(r'[a-f0-9]{64}',digest):raise ValueError('Invalid source hash')
        root=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            try:os.unlink(digest+'.original',dir_fd=root)
            except FileNotFoundError:pass
            os.fsync(root)
        finally:os.close(root)
