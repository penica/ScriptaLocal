"""Immutable, attempt-scoped stage artifacts; no client filenames become paths."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from uuid import UUID, uuid4


class ArtifactError(ValueError):pass


@dataclass(frozen=True)
class ArtifactRecord:
    id:UUID
    logical_key:str
    relative_path:str
    sha256:str
    bytes:int

    def __post_init__(self):
        if not isinstance(self.id,UUID) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',self.logical_key):
            raise ArtifactError('Invalid artifact identity')
        if not re.fullmatch(r'[a-f0-9]{64}',self.sha256) or type(self.bytes)is not int or self.bytes<0:
            raise ArtifactError('Invalid artifact checksum or size')
        self.parts()

    def parts(self):
        path=PurePosixPath(self.relative_path)
        parts=path.parts
        if path.is_absolute() or len(parts)!=5 or path.as_posix()!=self.relative_path:
            raise ArtifactError('Invalid artifact path')
        try:
            for index in [0,2,3]:
                if str(UUID(parts[index]))!=parts[index]:raise ValueError
            if str(int(parts[1]))!=parts[1] or int(parts[1])<1:raise ValueError
            filename=PurePosixPath(parts[4])
            if filename.suffix not in {'.json','.png','.bin','.txt'} or filename.stem!=str(self.id):raise ValueError
        except ValueError as exc:raise ArtifactError('Invalid artifact path components') from exc
        return parts

    def belongs_to(self,lease):
        return self.parts()[:4]==(str(lease.job_id),str(lease.revision),str(lease.stage_id),str(lease.token))

    def manifest(self):
        value=asdict(self);value['id']=str(self.id);return value


class ArtifactStore:
    def __init__(self,root:Path,*,maximum_bytes=64*1024*1024):
        if type(maximum_bytes)is not int or maximum_bytes<1:raise ValueError('Artifact byte limit must be positive')
        self.root=Path(root).resolve();self.maximum_bytes=maximum_bytes
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)

    @contextmanager
    def directory(self,parts,create):
        fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts:
                if create:
                    try:os.mkdir(part,0o700,dir_fd=fd);os.fsync(fd)
                    except FileExistsError:pass
                next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=next_fd
            yield fd
        finally:os.close(fd)

    def publish(self,lease,logical_key,payload:bytes,*,suffix='.json'):
        if not isinstance(payload,bytes):raise TypeError('Artifact payload must be bytes')
        if len(payload)>self.maximum_bytes:raise ArtifactError('Artifact exceeds byte limit')
        identifier=uuid4()
        relative='/'.join([str(lease.job_id),str(lease.revision),str(lease.stage_id),str(lease.token),str(identifier)+suffix])
        record=ArtifactRecord(identifier,logical_key,relative,hashlib.sha256(payload).hexdigest(),len(payload))
        parts=record.parts();temporary='.artifact-'+str(identifier)+'.tmp'
        with self.directory(parts[:-1],True) as directory:
            fd=None
            try:
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
                with os.fdopen(fd,'wb') as stream:
                    fd=None;stream.write(payload);stream.flush();os.fsync(stream.fileno())
                os.link(temporary,parts[-1],src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
                os.fsync(directory)
            finally:
                if fd is not None:os.close(fd)
                try:os.unlink(temporary,dir_fd=directory)
                except FileNotFoundError:pass
        return record

    def read(self,record:ArtifactRecord):
        if record.bytes>self.maximum_bytes:raise ArtifactError('Artifact exceeds byte limit')
        parts=record.parts()
        try:
            with self.directory(parts[:-1],False) as directory:
                fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                with os.fdopen(fd,'rb') as stream:
                    info=os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size!=record.bytes:
                        raise ArtifactError('Artifact is not a regular file with the recorded size')
                    content=stream.read(self.maximum_bytes+1)
        except OSError as exc:raise ArtifactError('Artifact is unavailable') from exc
        if len(content)!=record.bytes or hashlib.sha256(content).hexdigest()!=record.sha256:
            raise ArtifactError('Artifact checksum mismatch')
        return content
