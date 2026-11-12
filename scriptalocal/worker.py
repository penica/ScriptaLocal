"""Leased synchronous stage execution with independent fail-closed heartbeats."""
from dataclasses import dataclass
import math
import re
import threading

from .job_artifacts import ArtifactError
from .job_queue import LeaseLost,QueueError,principal
from .worker_control import ProcessingInterrupted


class WorkerUnavailable(RuntimeError):pass


class StageFailure(ValueError):
    def __init__(self,code,*,retryable=False):
        if not isinstance(code,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,99}',code):raise ValueError('Use a bounded public error code')
        if type(retryable)is not bool:raise ValueError('Retryable must be boolean')
        super().__init__(code);self.code=code;self.retryable=retryable


@dataclass(frozen=True)
class WorkerOutcome:
    status:str
    job_id:str|None=None
    stage:str|None=None
    error_code:str|None=None


class StageContext:
    def __init__(self,queue,lease):
        self.queue=queue;self.lease=lease;self.reason=None;self._lock=threading.Lock()

    def interrupt(self,reason):
        with self._lock:
            if self.reason is None:self.reason=reason

    def checkpoint(self):
        with self._lock:reason=self.reason
        if reason is not None:raise ProcessingInterrupted(reason)

    def inputs(self):
        self.checkpoint()
        try:return self.queue.input_artifacts(self.lease)
        except QueueError as exc:
            if exc.code=='cancel_requested':raise ProcessingInterrupted('cancel_requested') from None
            raise


class LeaseHeartbeat:
    def __init__(self,context,interval,join_timeout):
        self.context=context;self.interval=interval;self.join_timeout=join_timeout
        self.stopping=threading.Event();self.thread=None

    def pulse(self):
        try:
            details=self.context.queue.heartbeat(self.context.lease)
            if details['cancel_requested']:self.context.interrupt('cancel_requested')
        except LeaseLost:self.context.interrupt('lease_lost')
        except Exception:self.context.interrupt('heartbeat_unavailable')

    def start(self):
        self.pulse();self.context.checkpoint()
        def maintain():
            while not self.stopping.wait(self.interval):
                self.pulse()
                if self.context.reason is not None:return
        self.thread=threading.Thread(target=maintain,name='stage-heartbeat',daemon=True);self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(self.join_timeout)
            if self.thread.is_alive():
                self.context.interrupt('heartbeat_unavailable')
                raise WorkerUnavailable('Heartbeat did not stop; restart this worker')


class StageWorker:
    def __init__(self,queue,worker_id,handlers,*,configuration_sha256=None,heartbeat_interval=None,heartbeat_join_seconds=15):
        principal(worker_id)
        if configuration_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}',configuration_sha256):raise ValueError('Invalid configuration hash')
        if not handlers or set(handlers)-{'layout','segmentation','recognition'} or not all(callable(handler) for handler in handlers.values()):
            raise ValueError('Provide supported stage handlers')
        interval=min(10,queue.settings.lease_seconds/3) if heartbeat_interval is None else heartbeat_interval
        if not isinstance(interval,(int,float)) or isinstance(interval,bool) or not math.isfinite(interval) or not 0<interval<=queue.settings.lease_seconds/3:
            raise ValueError('Heartbeat interval must be positive and at most one third of the lease')
        if not isinstance(heartbeat_join_seconds,(int,float)) or isinstance(heartbeat_join_seconds,bool) or not math.isfinite(heartbeat_join_seconds) or heartbeat_join_seconds<=0:
            raise ValueError('Heartbeat join timeout must be positive')
        self.queue=queue;self.worker_id=worker_id;self.handlers=dict(handlers);self.configuration_sha256=configuration_sha256
        self.interval=interval;self.join_timeout=heartbeat_join_seconds;self.healthy=True

    def run_once(self):
        if not self.healthy:raise WorkerUnavailable('Restart the unhealthy worker before claiming more work')
        try:lease=self.queue.claim(self.worker_id,list(self.handlers),configuration_sha256=self.configuration_sha256)
        except Exception:
            self.healthy=False
            return WorkerOutcome('unavailable',error_code='queue_unavailable')
        if lease is None:return WorkerOutcome('idle')
        context=StageContext(self.queue,lease);heartbeat=LeaseHeartbeat(context,self.interval,self.join_timeout)
        failure=None;records=None
        try:
            heartbeat.start()
            records=[]
            for record in self.handlers[lease.stage](context):
                context.checkpoint()
                if len(records)>=self.queue.settings.maximum_artifacts:raise ArtifactError('Stage artifact count exceeds limit')
                records.append(record)
            context.checkpoint()
        except ProcessingInterrupted as exc:failure=exc
        except LeaseLost:failure=ProcessingInterrupted('lease_lost')
        except StageFailure as exc:failure=exc
        except ArtifactError:failure=StageFailure('invalid_stage_artifact')
        except OSError:failure=StageFailure('stage_storage_unavailable',retryable=True)
        except Exception:failure=StageFailure('stage_failed')
        finally:
            try:heartbeat.stop()
            except WorkerUnavailable:
                self.healthy=False;failure=ProcessingInterrupted('heartbeat_unavailable')
        # Revalidate before any completion/failure publication. A database outage
        # never authorizes a worker to assume it still owns a lease.
        if context.reason is not None and self.healthy:failure=ProcessingInterrupted(context.reason)
        if isinstance(failure,ProcessingInterrupted):
            if failure.reason=='heartbeat_unavailable':
                self.healthy=False;return WorkerOutcome('unavailable',str(lease.job_id),lease.stage,'heartbeat_unavailable')
            if failure.reason=='cancel_requested':
                try:self.queue.acknowledge_cancel(lease)
                except LeaseLost:return WorkerOutcome('lease_lost',str(lease.job_id),lease.stage,'lease_lost')
                except Exception:
                    self.healthy=False
                    return WorkerOutcome('unavailable',str(lease.job_id),lease.stage,'cancel_commit_unavailable')
                return WorkerOutcome('cancelled',str(lease.job_id),lease.stage)
            return WorkerOutcome('lease_lost',str(lease.job_id),lease.stage,'lease_lost')
        try:
            heartbeat.pulse();context.checkpoint()
            if failure is not None:
                state=self.queue.fail(lease,failure.code,retryable=failure.retryable)
                return WorkerOutcome('requeued' if state=='queued' else state,str(lease.job_id),lease.stage,failure.code)
            state=self.queue.complete(lease,records)
            return WorkerOutcome('cancelled' if state=='cancelled' else 'completed',str(lease.job_id),lease.stage)
        except ProcessingInterrupted as exc:
            if exc.reason=='cancel_requested':
                try:self.queue.acknowledge_cancel(lease)
                except LeaseLost:return WorkerOutcome('lease_lost',str(lease.job_id),lease.stage,'lease_lost')
                except Exception:
                    self.healthy=False
                    return WorkerOutcome('unavailable',str(lease.job_id),lease.stage,'cancel_commit_unavailable')
                return WorkerOutcome('cancelled',str(lease.job_id),lease.stage)
            if exc.reason=='heartbeat_unavailable':self.healthy=False
            return WorkerOutcome('unavailable' if exc.reason=='heartbeat_unavailable' else 'lease_lost',str(lease.job_id),lease.stage,exc.reason)
        except LeaseLost:return WorkerOutcome('lease_lost',str(lease.job_id),lease.stage,'lease_lost')
        except (ArtifactError,ValueError,TypeError):
            try:state=self.queue.fail(lease,'invalid_stage_artifact')
            except LeaseLost:return WorkerOutcome('lease_lost',str(lease.job_id),lease.stage,'lease_lost')
            except Exception:
                self.healthy=False
                return WorkerOutcome('unavailable',str(lease.job_id),lease.stage,'failure_commit_unavailable')
            return WorkerOutcome(state,str(lease.job_id),lease.stage,'invalid_stage_artifact')
        except Exception:
            self.healthy=False
            return WorkerOutcome('unavailable',str(lease.job_id),lease.stage,'stage_commit_unavailable')
