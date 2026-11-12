"""Exclusive, expiring filesystem readiness for a resident worker."""
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time


def atomic_json(path,document):
    path=Path(path)
    fd,temporary=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(document,stream,sort_keys=True,allow_nan=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass


class ResidentReadiness:
    def __init__(self,path,configuration_sha256,*,interval_seconds=5):
        if not re.fullmatch(r'[a-f0-9]{64}',configuration_sha256):raise ValueError('Invalid configuration hash')
        if isinstance(interval_seconds,bool) or not isinstance(interval_seconds,(int,float)) or not math.isfinite(interval_seconds) or interval_seconds<=0:
            raise ValueError('Readiness interval must be finite and positive')
        self.path=Path(path);self.configuration=configuration_sha256;self.interval=interval_seconds
        self.stop_event=threading.Event();self.thread=None;self.fd=None;self.failed=False

    def pulse(self):
        atomic_json(self.path,{'state':'ready','configuration_sha256':self.configuration,'last_seen':time.time()})

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.fd=os.open(str(self.path)+'.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            os.close(self.fd);self.fd=None;raise
        try:
            self.pulse()
            def maintain():
                while not self.stop_event.wait(self.interval):
                    try:self.pulse()
                    except Exception:
                        self.failed=True;return
            self.thread=threading.Thread(target=maintain,name='worker-readiness',daemon=True);self.thread.start()
        except Exception:
            self.__exit__(None,None,None);raise
        return self

    def check(self):
        if self.failed:raise RuntimeError('Readiness publication failed')

    def __exit__(self,*ignored):
        self.stop_event.set()
        if self.thread is not None:self.thread.join()
        if self.fd is not None:
            try:self.path.unlink(missing_ok=True)
            finally:os.close(self.fd);self.fd=None
