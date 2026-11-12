"""Actual restart/lease-fencing probe against the dedicated synthetic test DB."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from uuid import UUID

from scriptalocal.job_artifacts import ArtifactRecord,ArtifactStore
from scriptalocal.job_queue import JobQueue,QueueSettings,Lease,LeaseLost


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','verify']);args=parser.parse_args()
    if os.environ.get('SCRIPTALOCAL_ALLOW_DATABASE_TESTS')!='1':raise RuntimeError('Explicit database test opt-in required')
    queue=JobQueue(Path(os.environ['SCRIPTALOCAL_TEST_DSN_FILE']).read_text().strip(),
                   ArtifactStore(Path('/artifacts/files')),settings=QueueSettings(retry_delay_seconds=0))
    with queue.connect() as db:
        identity=db.execute('SELECT current_database() AS database,current_user AS role').fetchone()
        if identity!={'database':'scriptalocal_queue_validation','role':'scriptalocal_test'}:
            raise RuntimeError('Refusing work outside the isolated validation database')
    queue.migrate();state=Path('/artifacts/restart-probe.json')
    example=json.loads(Path('docs/api/examples/result.json').read_text())
    if args.action=='prepare':
        with queue.connect() as db:
            if db.execute("SELECT count(*) AS count FROM sl_jobs WHERE status IN ('queued','running')").fetchone()['count']:
                raise RuntimeError('Validation requires no existing active test jobs')
        job,_=queue.create('restart-validation',example['source'],{},example['provenance']['configuration_sha256'],idempotency_key='restart-probe-001')
        lease=queue.claim('lost-worker',['layout'])
        if lease is None or lease.job_id!=job['id']:raise AssertionError('Unexpected claim')
        artifact=queue.artifacts.publish(lease,'output',b'{"phase":"before_restart"}')
        with queue.connect() as db:
            db.execute("UPDATE sl_stages SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(lease.stage_id,))
        state.write_text(json.dumps({'lease':asdict(lease),'artifact':artifact.manifest()},default=str,indent=2)+'\n')
        print(json.dumps({'prepared':True,'running_jobs':1,'durable_artifact_bytes':artifact.bytes}));return
    saved=json.loads(state.read_text());values=saved['lease']
    for key in ['job_id','stage_id','token']:values[key]=UUID(values[key])
    old=Lease(**values);old_artifact=ArtifactRecord(**{**saved['artifact'],'id':UUID(saved['artifact']['id'])})
    job,replay=queue.create('restart-validation',example['source'],{},example['provenance']['configuration_sha256'],idempotency_key='restart-probe-001')
    assert replay and job['id']==old.job_id
    try:queue.complete(old,[old_artifact]);raise AssertionError('Expired worker published')
    except LeaseLost:pass
    assert queue.recover_expired()==1
    recovered=queue.claim('recovered-worker',['layout'])
    assert recovered.job_id==old.job_id and recovered.token!=old.token and recovered.attempts==2
    new_artifact=queue.artifacts.publish(recovered,'output',b'{"phase":"after_restart"}')
    assert queue.complete(recovered,[new_artifact])=='completed'
    assert queue.artifacts.read(old_artifact)==b'{"phase":"before_restart"}'
    with queue.connect() as db:
        assert db.execute('SELECT count(*) AS count FROM sl_artifacts WHERE id=%s',(old_artifact.id,)).fetchone()['count']==0
        assert db.execute('SELECT count(*) AS count FROM sl_artifacts WHERE stage_id=%s',(old.stage_id,)).fetchone()['count']==1
        version=db.execute('SELECT version() AS version').fetchone()['version']
    queue.cancel(old.job_id,'restart-validation')
    print(json.dumps({'database':identity['database'],'server_version':version,
                      'idempotency_after_restart':'passed','old_worker_publication':'rejected',
                      'expired_lease_reclaimed':'passed','new_attempt_number':recovered.attempts,
                      'one_committed_output':'passed','old_attempt_bytes_unchanged':'passed',
                      'remaining_old_attempt_file':'unreferenced orphan, not a published result',
                      'synthetic_job_cancelled_after_probe':True},indent=2))


if __name__=='__main__':main()
