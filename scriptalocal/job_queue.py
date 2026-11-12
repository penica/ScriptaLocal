"""PostgreSQL stage queue with durable idempotency and lease fencing."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID,uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import JobOptions,JobResult,SourceImage
from .job_artifacts import ArtifactError,ArtifactRecord,ArtifactStore

STAGES=('layout','segmentation','recognition')


class QueueError(ValueError):
    def __init__(self,code,message):super().__init__(message);self.code=code


class LeaseLost(QueueError):
    def __init__(self):super().__init__('lease_lost','Worker lease is no longer valid')


@dataclass(frozen=True)
class QueueSettings:
    maximum_active_jobs:int=1000
    maximum_attempts:int=3
    lease_seconds:int=60
    retry_delay_seconds:int=2
    maximum_artifacts:int=4096
    maximum_completion_bytes:int=256*1024*1024
    database_statement_timeout_ms:int=5000
    database_lock_timeout_ms:int=2000
    database_connect_timeout_seconds:int=5
    database_tcp_user_timeout_ms:int=5000
    database_keepalive_idle_seconds:int=5
    database_keepalive_interval_seconds:int=2
    database_keepalive_count:int=2

    def __post_init__(self):
        for key,value in vars(self).items():
            if type(value)is not int or value<(0 if key=='retry_delay_seconds' else 1):
                raise ValueError('Queue limits must be positive integers, with a nonnegative retry delay')


@dataclass(frozen=True)
class Lease:
    job_id:UUID
    revision:int
    stage_id:UUID
    stage:str
    token:UUID
    worker_id:str
    attempts:int
    source:dict
    options:dict
    configuration_sha256:str

    def __post_init__(self):
        if not all(isinstance(value,UUID) for value in [self.job_id,self.stage_id,self.token]):raise ValueError('Lease IDs must be UUIDs')
        if type(self.revision)is not int or self.revision<1 or self.stage not in STAGES:raise ValueError('Invalid lease stage/revision')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def principal(value):
    if not isinstance(value,str) or not value.strip() or len(value)>128 or not value.isprintable():raise ValueError('Invalid principal or worker ID')
    return value


class JobQueue:
    def __init__(self,dsn,artifacts:ArtifactStore,*,settings=QueueSettings()):
        self._dsn=dsn;self.artifacts=artifacts;self.settings=settings

    def connect(self):
        return psycopg.connect(self._dsn,row_factory=dict_row,connect_timeout=self.settings.database_connect_timeout_seconds,
                               options=f'-c timezone=UTC -c application_name=scriptalocal -c statement_timeout={self.settings.database_statement_timeout_ms} -c lock_timeout={self.settings.database_lock_timeout_ms}',
                               tcp_user_timeout=self.settings.database_tcp_user_timeout_ms,keepalives=1,
                               keepalives_idle=self.settings.database_keepalive_idle_seconds,
                               keepalives_interval=self.settings.database_keepalive_interval_seconds,
                               keepalives_count=self.settings.database_keepalive_count)

    def migrate(self):
        files=sorted((Path(__file__).parent/'migrations').glob('*.sql'))
        versions={int(file.name.split('_',1)[0]):file for file in files}
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(7346001)')
            db.execute('CREATE TABLE IF NOT EXISTS sl_schema_migrations(version integer PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT clock_timestamp())')
            history=db.execute('SELECT version,sha256 FROM sl_schema_migrations ORDER BY version').fetchall()
            applied={row['version']:row['sha256'] for row in history}
            if set(applied)-set(versions):raise QueueError('unknown_migration','Database has an unknown migration version')
            for version,file in versions.items():
                content=file.read_bytes();checksum=hashlib.sha256(content).hexdigest()
                if version in applied:
                    if applied[version]!=checksum:raise QueueError('migration_checksum_mismatch','Applied migration differs from source')
                else:
                    db.execute(content.decode(),prepare=False)
                    db.execute('INSERT INTO sl_schema_migrations(version,sha256) VALUES(%s,%s)',(version,checksum))

    def schema_current(self):
        """Read-only readiness check; never apply migrations from an HTTP probe."""
        expected={int(file.name.split('_',1)[0]):hashlib.sha256(file.read_bytes()).hexdigest()
                  for file in (Path(__file__).parent/'migrations').glob('*.sql')}
        with self.connect() as db:
            history=db.execute('SELECT version,sha256 FROM sl_schema_migrations').fetchall()
            if {row['version']:row['sha256'] for row in history}!=expected:return False
            for table in ('sl_jobs','sl_stages','sl_artifacts','sl_deletions'):
                # Table names are fixed literals, never request input.
                db.execute('SELECT 1 FROM '+table+' LIMIT 0')
        return True

    def create(self,owner,source,options,configuration_sha256,*,idempotency_key=None,expected_existing_id=None):
        owner=principal(owner);source=SourceImage.model_validate(source).model_dump(mode='json')
        options=JobOptions.model_validate(options).model_dump(mode='json')
        if not re.fullmatch(r'[a-f0-9]{64}',configuration_sha256):raise ValueError('Invalid configuration hash')
        if idempotency_key is not None and (not isinstance(idempotency_key,str) or not re.fullmatch(r'[!-~]{1,128}',idempotency_key)):
            raise ValueError('Invalid idempotency key')
        fingerprint=canonical_hash({'source':source,'options':options,'configuration_sha256':configuration_sha256})
        with self.connect() as db:
            # The small create transaction serializes admission and key checks;
            # worker processing never holds this lock.
            db.execute('SELECT pg_advisory_xact_lock(7346002)')
            if idempotency_key is not None:
                old=db.execute('SELECT * FROM sl_jobs WHERE owner=%s AND idempotency_key=%s',(owner,idempotency_key)).fetchone()
                if old:
                    if expected_existing_id is not None and old['id']!=expected_existing_id:raise QueueError('idempotency_state_changed','Previous operation identity changed')
                    if old['fingerprint']!=fingerprint:raise QueueError('idempotency_conflict','Key was already used with different input')
                    return old,True
            if expected_existing_id is not None:raise QueueError('idempotency_state_changed','Previous operation was deleted; reconcile before retrying')
            count=db.execute("SELECT count(*) AS count FROM sl_jobs WHERE status IN ('queued','running')").fetchone()['count']
            if count>=self.settings.maximum_active_jobs:raise QueueError('queue_full','Job queue has reached its active-job limit')
            identifier=uuid4()
            db.execute("INSERT INTO sl_jobs(id,owner,idempotency_key,fingerprint,configuration_sha256,source,options,status,current_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',1)",
                       (identifier,owner,idempotency_key,fingerprint,configuration_sha256,Jsonb(source),Jsonb(options)))
            db.execute("INSERT INTO sl_revisions(job_id,revision,options,state) VALUES(%s,1,%s,'pending')",(identifier,Jsonb(options)))
            self._new_stage(db,identifier,1,'layout')
            return db.execute('SELECT * FROM sl_jobs WHERE id=%s',(identifier,)).fetchone(),False

    def _new_stage(self,db,job,revision,name):
        db.execute("INSERT INTO sl_stages(id,job_id,revision,name,status,max_attempts) VALUES(%s,%s,%s,%s,'queued',%s)",
                   (uuid4(),job,revision,name,self.settings.maximum_attempts))

    def _job(self,db,identifier,owner=None,*,lock=False):
        parameters=[identifier];query='SELECT * FROM sl_jobs WHERE id=%s'
        if owner is not None:query+=' AND owner=%s';parameters.append(principal(owner))
        if lock:query+=' FOR UPDATE'
        row=db.execute(query,parameters).fetchone()
        if row is None:raise QueueError('not_found','Job not found')
        return row

    def get(self,identifier,owner):
        with self.connect() as db:return self._job(db,identifier,owner)

    def claim(self,worker_id,capabilities=STAGES,*,configuration_sha256=None):
        worker_id=principal(worker_id);capabilities=list(capabilities)
        if configuration_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}',configuration_sha256):raise ValueError('Invalid configuration hash')
        if not capabilities or any(name not in STAGES for name in capabilities):raise ValueError('Invalid worker capabilities')
        self.recover_expired()
        with self.connect() as db:
            job=db.execute("""SELECT j.* FROM sl_jobs j WHERE j.status='queued' AND NOT j.cancel_requested
             AND (%s::text IS NULL OR j.configuration_sha256=%s)
             AND EXISTS(SELECT 1 FROM sl_stages s WHERE s.job_id=j.id AND s.revision=j.current_revision
              AND s.status='queued' AND s.available_at<=clock_timestamp() AND s.name=ANY(%s))
             ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1""",(configuration_sha256,configuration_sha256,capabilities)).fetchone()
            if job is None:return None
            stage=db.execute("SELECT * FROM sl_stages WHERE job_id=%s AND revision=%s AND status='queued' AND name=ANY(%s) ORDER BY created_at FOR UPDATE LIMIT 1",
                             (job['id'],job['current_revision'],capabilities)).fetchone()
            token=uuid4()
            stage=db.execute("""UPDATE sl_stages SET status='running',attempts=attempts+1,worker_id=%s,lease_token=%s,
               lease_expires_at=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp()
               WHERE id=%s RETURNING *""",(worker_id,token,self.settings.lease_seconds,stage['id'])).fetchone()
            db.execute("UPDATE sl_jobs SET status='running',updated_at=clock_timestamp() WHERE id=%s",(job['id'],))
            return Lease(job['id'],job['current_revision'],stage['id'],stage['name'],token,worker_id,
                         stage['attempts'],job['source'],job['options'],job['configuration_sha256'])

    def _locked(self,db,lease):
        # Always lock the job before its stage, including cancellation/recovery.
        job=self._job(db,lease.job_id,lock=True)
        stage=db.execute('SELECT *,lease_expires_at>clock_timestamp() AS live FROM sl_stages WHERE id=%s AND job_id=%s AND revision=%s FOR UPDATE',
                         (lease.stage_id,lease.job_id,lease.revision)).fetchone()
        if stage is None:raise LeaseLost()
        return job,stage

    def _assert_live(self,job,stage,lease):
        if job['current_revision']!=lease.revision or job['status']!='running' or stage['status']!='running' or not stage['live'] or (
                stage['lease_token']!=lease.token or stage['worker_id']!=lease.worker_id or stage['name']!=lease.stage):
            raise LeaseLost()

    def heartbeat(self,lease):
        with self.connect() as db:
            job,stage=self._locked(db,lease);self._assert_live(job,stage,lease)
            if job['cancel_requested']:return {'cancel_requested':True,'lease_expires_at':stage['lease_expires_at']}
            deadline=db.execute("UPDATE sl_stages SET lease_expires_at=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp() WHERE id=%s RETURNING lease_expires_at",
                                (self.settings.lease_seconds,stage['id'])).fetchone()['lease_expires_at']
            db.execute('UPDATE sl_jobs SET updated_at=clock_timestamp() WHERE id=%s',(job['id'],))
            return {'cancel_requested':False,'lease_expires_at':deadline}

    def _cancel_current(self,db,job):
        db.execute("UPDATE sl_stages SET status='cancelled',worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() WHERE job_id=%s AND revision=%s AND status IN ('queued','running')",(job['id'],job['current_revision']))
        db.execute("UPDATE sl_revisions SET state='cancelled' WHERE job_id=%s AND revision=%s AND state='pending'",(job['id'],job['current_revision']))
        db.execute("UPDATE sl_jobs SET status='cancelled',cancel_requested=true,error_code=NULL,updated_at=clock_timestamp() WHERE id=%s",(job['id'],))

    def cancel(self,identifier,owner):
        with self.connect() as db:
            job=self._job(db,identifier,owner,lock=True)
            if job['status']=='cancelled':return job
            if job['status'] in {'succeeded','failed'}:raise QueueError('terminal_job','A terminal job cannot be cancelled')
            if job['status']=='queued':self._cancel_current(db,job)
            else:db.execute('UPDATE sl_jobs SET cancel_requested=true,updated_at=clock_timestamp() WHERE id=%s',(job['id'],))
            return self._job(db,identifier,owner)

    def acknowledge_cancel(self,lease):
        with self.connect() as db:
            job,stage=self._locked(db,lease);self._assert_live(job,stage,lease)
            if not job['cancel_requested']:raise QueueError('cancellation_not_requested','Cancellation was not requested')
            self._cancel_current(db,job)

    def _failure(self,db,job,stage,code,retryable):
        retry=retryable and stage['attempts']<stage['max_attempts']
        status='queued' if retry else 'failed'
        db.execute("""UPDATE sl_stages SET status=%s,worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,
          last_error_code=%s,available_at=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp() WHERE id=%s""",
          (status,code,self.settings.retry_delay_seconds,stage['id']))
        db.execute('UPDATE sl_jobs SET status=%s,error_code=%s,updated_at=clock_timestamp() WHERE id=%s',
                   (status,None if retry else code,job['id']))
        if not retry:db.execute("UPDATE sl_revisions SET state='failed' WHERE job_id=%s AND revision=%s",(job['id'],job['current_revision']))
        return status

    def fail(self,lease,code,*,retryable=False):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,99}',code):raise ValueError('Use a public error code, not an exception message')
        with self.connect() as db:
            job,stage=self._locked(db,lease);self._assert_live(job,stage,lease)
            if job['cancel_requested']:self._cancel_current(db,job);return 'cancelled'
            return self._failure(db,job,stage,code,retryable)

    def recover_expired(self,*,limit=100):
        if type(limit)is not int or limit<1:raise ValueError('Recovery limit must be positive')
        with self.connect() as db:
            jobs=db.execute("""SELECT j.* FROM sl_jobs j WHERE j.status='running' AND EXISTS(
              SELECT 1 FROM sl_stages s WHERE s.job_id=j.id AND s.revision=j.current_revision
              AND s.status='running' AND s.lease_expires_at<=clock_timestamp())
              ORDER BY j.updated_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT %s""",(limit,)).fetchall()
            for job in jobs:
                stage=db.execute("SELECT * FROM sl_stages WHERE job_id=%s AND revision=%s AND status='running' AND lease_expires_at<=clock_timestamp() FOR UPDATE",(job['id'],job['current_revision'])).fetchone()
                if stage is None:continue
                if job['cancel_requested']:self._cancel_current(db,job)
                else:self._failure(db,job,stage,'lease_attempts_exhausted' if stage['attempts']>=stage['max_attempts'] else 'lease_expired',True)
            return len(jobs)

    def complete(self,lease,records):
        records=list(records)
        if not records or len(records)>self.settings.maximum_artifacts or sum(record.bytes for record in records)>self.settings.maximum_completion_bytes:
            raise ArtifactError('Completion artifact limits exceeded')
        if len({record.logical_key for record in records})!=len(records) or len({record.id for record in records})!=len(records):
            raise ArtifactError('Completion artifacts must have unique IDs and logical keys')
        if any(not record.belongs_to(lease) for record in records):raise ArtifactError('Artifact belongs to a different job, revision or attempt')
        primary_key='result' if lease.stage=='recognition' else 'output'
        primary=next((record for record in records if record.logical_key==primary_key),None)
        if primary is None:raise ArtifactError('Stage completion lacks its primary output')
        payload=None
        for record in records:
            content=self.artifacts.read(record)
            if record.id==primary.id:payload=content
        final=JobResult.model_validate_json(payload) if lease.stage=='recognition' else None
        if final is None and not isinstance(json.loads(payload),dict):raise ArtifactError('Intermediate output must be a JSON object')
        fingerprint=canonical_hash([record.manifest() for record in sorted(records,key=lambda item:item.logical_key)])
        with self.connect() as db:
            job,stage=self._locked(db,lease)
            if stage['status']=='succeeded' and stage['completion_token']==lease.token:
                if stage['completion_fingerprint']!=fingerprint:raise QueueError('completion_conflict','Completed attempt was replayed with different artifacts')
                return 'already_completed'
            self._assert_live(job,stage,lease)
            if job['cancel_requested']:self._cancel_current(db,job);return 'cancelled'
            if final and (final.job_id!=job['id'] or final.revision!=job['current_revision'] or
                          final.source.model_dump(mode='json')!=job['source'] or
                          final.provenance.configuration_sha256!=job['configuration_sha256']):
                raise ArtifactError('Final result does not match the job input, configuration or revision')
            for record in records:
                db.execute('INSERT INTO sl_artifacts(id,job_id,revision,stage_id,lease_token,logical_key,relative_path,sha256,bytes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (record.id,job['id'],lease.revision,lease.stage_id,lease.token,record.logical_key,record.relative_path,record.sha256,record.bytes))
            db.execute("""UPDATE sl_stages SET status='succeeded',worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,
               completion_token=%s,completion_fingerprint=%s,output_artifact_id=%s,updated_at=clock_timestamp() WHERE id=%s""",
               (lease.token,fingerprint,primary.id,stage['id']))
            if final:
                db.execute("UPDATE sl_revisions SET state='succeeded',result_artifact_id=%s WHERE job_id=%s AND revision=%s",(primary.id,job['id'],lease.revision))
                db.execute("UPDATE sl_jobs SET status='succeeded',error_code=NULL,review_required=%s,warnings=%s,updated_at=clock_timestamp() WHERE id=%s",
                           (final.review_required,Jsonb([warning.model_dump(mode='json') for warning in final.warnings]),job['id']))
            else:
                self._new_stage(db,job['id'],lease.revision,STAGES[STAGES.index(lease.stage)+1])
                db.execute("UPDATE sl_jobs SET status='queued',updated_at=clock_timestamp() WHERE id=%s",(job['id'],))
            return 'completed'

    def input_artifacts(self,lease):
        """Committed predecessor artifacts only, under a current live worker lease."""
        with self.connect() as db:
            job,stage=self._locked(db,lease);self._assert_live(job,stage,lease)
            if job['cancel_requested']:raise QueueError('cancel_requested','Cancellation was requested')
            index=STAGES.index(lease.stage)
            if index==0:return ()
            rows=db.execute("""SELECT a.* FROM sl_stages s JOIN sl_artifacts a ON a.stage_id=s.id
              WHERE s.job_id=%s AND s.revision=%s AND s.name=%s AND s.status='succeeded'
              ORDER BY a.logical_key LIMIT %s""",(lease.job_id,lease.revision,STAGES[index-1],self.settings.maximum_artifacts+1)).fetchall()
            if not rows or len(rows)>self.settings.maximum_artifacts or not any(row['logical_key']=='output' for row in rows):
                raise QueueError('stage_input_unavailable','Committed predecessor output is unavailable')
            return tuple(ArtifactRecord(row['id'],row['logical_key'],row['relative_path'],row['sha256'],row['bytes']) for row in rows)

    def find_idempotent(self,owner,key):
        owner=principal(owner)
        if not isinstance(key,str) or not re.fullmatch(r'[!-~]{1,128}',key):raise ValueError('Invalid idempotency key')
        with self.connect() as db:
            return db.execute('SELECT * FROM sl_jobs WHERE owner=%s AND idempotency_key=%s',(owner,key)).fetchone()

    def request_deletion(self,identifier,owner):
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(7346002)')
            job=self._job(db,identifier,owner,lock=True)
            if job['status']=='running':raise QueueError('job_running','Cancel and acknowledge the running job before deletion')
            records=db.execute('SELECT * FROM sl_artifacts WHERE job_id=%s ORDER BY relative_path',(identifier,)).fetchall()
            manifest=[ArtifactRecord(row['id'],row['logical_key'],row['relative_path'],row['sha256'],row['bytes']).manifest() for row in records]
            request_id=uuid4()
            db.execute('INSERT INTO sl_deletions(id,job_id,owner,source_sha256,artifacts) VALUES(%s,%s,%s,%s,%s)',
                       (request_id,identifier,job['owner'],job['source']['sha256'],Jsonb(manifest)))
            db.execute('DELETE FROM sl_jobs WHERE id=%s',(identifier,))
            return request_id

    def result_reference(self,identifier,owner):
        with self.connect() as db:
            job=self._job(db,identifier,owner)
            if job['status']!='succeeded':raise QueueError('result_not_ready','A completed result is not available')
            record=db.execute('SELECT a.* FROM sl_revisions r JOIN sl_artifacts a ON a.id=r.result_artifact_id WHERE r.job_id=%s AND r.revision=%s',
                              (identifier,job['current_revision'])).fetchone()
            if record is None:raise QueueError('result_unavailable','Result metadata is unavailable')
            return ArtifactRecord(record['id'],record['logical_key'],record['relative_path'],record['sha256'],record['bytes'])
