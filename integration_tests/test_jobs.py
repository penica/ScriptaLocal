from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from scriptalocal.contracts import JobResult
from scriptalocal.job_artifacts import ArtifactError,ArtifactRecord,ArtifactStore
from scriptalocal.job_queue import JobQueue,QueueError,QueueSettings,LeaseLost

EXAMPLES=Path(__file__).resolve().parents[1]/'docs/api/examples'


class JobQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('SCRIPTALOCAL_ALLOW_DATABASE_TESTS')!='1':
            raise RuntimeError('Use the isolated jobs-validation Compose profile; no default database is accepted')
        cls.dsn=Path(os.environ['SCRIPTALOCAL_TEST_DSN_FILE']).read_text().strip()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.store=ArtifactStore(Path(self.directory.name))
        self.queue=JobQueue(self.dsn,self.store,settings=QueueSettings(retry_delay_seconds=0))
        with self.queue.connect() as db:
            identity=db.execute('SELECT current_database() AS database,current_user AS role').fetchone()
        if identity!={'database':'scriptalocal_queue_validation','role':'scriptalocal_test'}:
            raise RuntimeError('Refusing mutation outside the dedicated validation database/role')
        self.queue.migrate();self.clear()
        self.example=json.loads((EXAMPLES/'result.json').read_text())
        self.source=self.example['source'];self.configuration=self.example['provenance']['configuration_sha256']

    def tearDown(self):
        self.clear();self.directory.cleanup()

    def clear(self):
        with self.queue.connect() as db:db.execute('TRUNCATE sl_revision_edits,sl_deletions,sl_artifacts,sl_stages,sl_revisions,sl_jobs')

    def create(self,owner='alice',key=None,**options):
        return self.queue.create(owner,self.source,options,self.configuration,idempotency_key=key)[0]

    def expire(self,lease):
        with self.queue.connect() as db:
            db.execute("UPDATE sl_stages SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(lease.stage_id,))

    def output(self,lease):
        return self.store.publish(lease,'output',json.dumps({'stage':lease.stage}).encode())

    def recognition(self):
        job=self.create()
        for expected in ['layout','segmentation']:
            lease=self.queue.claim('worker',[expected]);self.assertEqual(lease.stage,expected)
            self.queue.complete(lease,[self.output(lease)])
        return job,self.queue.claim('gpu-0',['recognition'])

    def final(self,lease,**changes):
        data=json.loads((EXAMPLES/'result.json').read_text())
        data.update(job_id=str(lease.job_id),revision=lease.revision);data.update(changes)
        return self.store.publish(lease,'result',json.dumps(data).encode())

    def test_migrations_are_idempotent_and_reject_changed_checksums(self):
        self.queue.migrate()
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_schema_migrations').fetchone()['count'],3)
            checksum=db.execute('SELECT sha256 FROM sl_schema_migrations WHERE version=1').fetchone()['sha256']
            db.execute("UPDATE sl_schema_migrations SET sha256=%s WHERE version=1",('0'*64,))
        try:
            with self.assertRaises(QueueError) as caught:self.queue.migrate()
            self.assertEqual(caught.exception.code,'migration_checksum_mismatch')
        finally:
            with self.queue.connect() as db:db.execute('UPDATE sl_schema_migrations SET sha256=%s WHERE version=1',(checksum,))

    def test_idempotency_survives_new_client_and_is_scoped_to_owner(self):
        first,replayed=self.queue.create('alice',self.source,{},self.configuration,idempotency_key='request-1')
        self.assertFalse(replayed)
        second,replayed=JobQueue(self.dsn,self.store).create('alice',self.source,{},self.configuration,idempotency_key='request-1')
        self.assertTrue(replayed);self.assertEqual(first['id'],second['id'])
        other=self.create('bob','request-1');self.assertNotEqual(first['id'],other['id'])
        with self.assertRaises(QueueError) as caught:
            self.queue.create('alice',self.source,{'client_reference':'different'},self.configuration,idempotency_key='request-1')
        self.assertEqual(caught.exception.code,'idempotency_conflict')

    def test_expected_existing_identity_blocks_deleted_key_recreation(self):
        first=self.create('alice','immutable-operation')
        self.queue.request_deletion(first['id'],'alice')
        with self.assertRaises(QueueError) as caught:
            self.queue.create('alice',self.source,{},self.configuration,idempotency_key='immutable-operation',expected_existing_id=first['id'])
        self.assertEqual(caught.exception.code,'idempotency_state_changed')
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_jobs').fetchone()['count'],0)

    def test_concurrent_duplicate_submissions_create_one_initial_stage(self):
        def submit(_):return self.queue.create('alice',self.source,{},self.configuration,idempotency_key='same')[0]['id']
        with ThreadPoolExecutor(max_workers=8) as pool:identifiers=list(pool.map(submit,range(16)))
        self.assertEqual(len(set(identifiers)),1)
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_stages').fetchone()['count'],1)
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_revisions').fetchone()['count'],1)

    def test_capacity_is_atomic_and_replay_still_works_when_full(self):
        queue=JobQueue(self.dsn,self.store,settings=QueueSettings(maximum_active_jobs=1,retry_delay_seconds=0))
        first,_=queue.create('alice',self.source,{},self.configuration,idempotency_key='first')
        same,replay=queue.create('alice',self.source,{},self.configuration,idempotency_key='first')
        self.assertTrue(replay);self.assertEqual(same['id'],first['id'])
        with self.assertRaises(QueueError) as caught:queue.create('bob',self.source,{},self.configuration)
        self.assertEqual(caught.exception.code,'queue_full')

    def test_concurrent_admission_never_exceeds_capacity(self):
        queue=JobQueue(self.dsn,self.store,settings=QueueSettings(maximum_active_jobs=3,retry_delay_seconds=0))
        def submit(index):
            try:
                queue.create('alice',self.source,{'client_reference':str(index)},self.configuration)
                return 'created'
            except QueueError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:outcomes=list(pool.map(submit,range(8)))
        self.assertEqual(outcomes.count('created'),3);self.assertEqual(outcomes.count('queue_full'),5)

    def test_concurrent_workers_claim_each_stage_once(self):
        for i in range(16):self.create(client_reference=str(i))
        def work(index):
            claims=[]
            while True:
                lease=self.queue.claim('worker-'+str(index),['layout'])
                if lease is None:return claims
                claims.append(lease.stage_id)
        with ThreadPoolExecutor(max_workers=8) as pool:claims=[value for values in pool.map(work,range(8)) for value in values]
        self.assertEqual(len(claims),16);self.assertEqual(len(set(claims)),16)
        with self.queue.connect() as db:
            self.assertEqual(db.execute("SELECT max(attempts) AS maximum FROM sl_stages").fetchone()['maximum'],1)

    def test_worker_capabilities_do_not_claim_other_stage_types(self):
        self.create();self.assertIsNone(self.queue.claim('recognizer',['recognition']))
        self.assertEqual(self.queue.claim('layout-worker',['layout']).stage,'layout')

    def test_heartbeat_recovery_and_stale_worker_fencing(self):
        job=self.create();old=self.queue.claim('old',['layout'])
        renewed=self.queue.heartbeat(old);self.assertFalse(renewed['cancel_requested'])
        artifact=self.output(old);self.expire(old)
        with self.assertRaises(LeaseLost):self.queue.heartbeat(old)
        self.assertEqual(self.queue.recover_expired(),1)
        new=self.queue.claim('new',['layout']);self.assertNotEqual(old.token,new.token);self.assertEqual(new.attempts,2)
        with self.assertRaises(LeaseLost):self.queue.complete(old,[artifact])
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)
        self.assertEqual(self.queue.complete(new,[self.output(new)]),'completed')
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'queued')

    def test_crashed_worker_retries_are_bounded(self):
        queue=JobQueue(self.dsn,self.store,settings=QueueSettings(maximum_attempts=2,retry_delay_seconds=0))
        job=queue.create('alice',self.source,{},self.configuration)[0]
        first=queue.claim('one',['layout']);self.expire(first);queue.recover_expired()
        second=queue.claim('two',['layout']);self.expire(second);queue.recover_expired()
        self.assertIsNone(queue.claim('three',['layout']))
        row=queue.get(job['id'],'alice');self.assertEqual(row['status'],'failed');self.assertEqual(row['error_code'],'lease_attempts_exhausted')

    def test_explicit_failures_and_retry_delay(self):
        job=self.create();lease=self.queue.claim('worker',['layout'])
        delayed=JobQueue(self.dsn,self.store,settings=QueueSettings(retry_delay_seconds=60))
        self.assertEqual(delayed.fail(lease,'gpu_oom',retryable=True),'queued')
        self.assertIsNone(delayed.claim('next',['layout']))
        with self.queue.connect() as db:db.execute("UPDATE sl_stages SET available_at=clock_timestamp()-interval '1 second'")
        lease=delayed.claim('next',['layout']);self.assertEqual(delayed.fail(lease,'invalid_segmentation',retryable=False),'failed')
        self.assertEqual(delayed.get(job['id'],'alice')['error_code'],'invalid_segmentation')

    def test_queued_cancel_is_idempotent_and_ownership_scoped(self):
        job=self.create()
        with self.assertRaises(QueueError) as caught:self.queue.cancel(job['id'],'bob')
        self.assertEqual(caught.exception.code,'not_found')
        self.assertEqual(self.queue.cancel(job['id'],'alice')['status'],'cancelled')
        self.assertEqual(self.queue.cancel(job['id'],'alice')['status'],'cancelled')
        self.assertIsNone(self.queue.claim('worker'))

    def test_running_cancel_blocks_late_publication(self):
        job=self.create();lease=self.queue.claim('worker',['layout']);artifact=self.output(lease)
        self.assertEqual(self.queue.cancel(job['id'],'alice')['status'],'running')
        self.assertTrue(self.queue.heartbeat(lease)['cancel_requested'])
        self.assertEqual(self.queue.complete(lease,[artifact]),'cancelled')
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'cancelled')
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)

    def test_worker_cancel_ack_and_cancelled_expired_worker(self):
        job=self.create();lease=self.queue.claim('worker',['layout'])
        with self.assertRaises(QueueError):self.queue.acknowledge_cancel(lease)
        self.queue.cancel(job['id'],'alice');self.queue.acknowledge_cancel(lease)
        other=self.create(client_reference='other');lease=self.queue.claim('worker',['layout'])
        self.queue.cancel(other['id'],'alice');self.expire(lease);self.queue.recover_expired()
        self.assertEqual(self.queue.get(other['id'],'alice')['status'],'cancelled')

    def test_completion_replays_do_not_duplicate_outputs_or_next_stages(self):
        self.create();lease=self.queue.claim('worker',['layout']);artifact=self.output(lease)
        self.assertEqual(self.queue.complete(lease,[artifact]),'completed')
        self.assertEqual(self.queue.complete(lease,[artifact]),'already_completed')
        with self.assertRaises(QueueError):self.queue.complete(lease,[self.output(lease)])
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_stages').fetchone()['count'],2)
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],1)

    def test_final_result_is_schema_checked_and_owner_scoped(self):
        job,lease=self.recognition();artifact=self.final(lease)
        self.assertEqual(self.queue.complete(lease,[artifact]),'completed')
        row=self.queue.get(job['id'],'alice');self.assertEqual(row['status'],'succeeded');self.assertTrue(row['review_required'])
        self.assertEqual(JobResult.model_validate_json(self.store.read(self.queue.result_reference(job['id'],'alice'))).job_id,job['id'])
        with self.assertRaises(QueueError):self.queue.result_reference(job['id'],'bob')
        with self.assertRaises(QueueError):self.queue.cancel(job['id'],'alice')
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_revisions').fetchone()['count'],1)

    def test_mismatched_final_result_and_foreign_artifacts_are_rejected(self):
        job,lease=self.recognition()
        with self.assertRaises(ArtifactError):self.queue.complete(lease,[self.final(lease,job_id=str(uuid4()))])
        foreign=replace(lease,job_id=uuid4())
        with self.assertRaises(ArtifactError):self.queue.complete(lease,[self.store.publish(foreign,'result',b'{}')])
        with self.queue.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS count FROM sl_artifacts WHERE stage_id=%s",(lease.stage_id,)).fetchone()['count'],0)

    def test_artifact_tampering_and_path_traversal_are_rejected(self):
        self.create();lease=self.queue.claim('worker',['layout']);artifact=self.output(lease)
        path=self.store.root/artifact.relative_path;path.write_bytes(b'changed')
        with self.assertRaises(ArtifactError):self.queue.complete(lease,[artifact])
        with self.assertRaises(ArtifactError):ArtifactRecord(uuid4(),'output','../outside','0'*64,0)
        path.unlink();outside=Path(self.directory.name)/'outside';outside.write_bytes(b'x');path.symlink_to(outside)
        with self.assertRaises(ArtifactError):self.store.read(artifact)

    def test_artifact_parent_symlinks_and_failed_publication_do_not_escape_or_leave_files(self):
        self.create();lease=self.queue.claim('worker',['layout'])
        outside=Path(self.directory.name)/'external';outside.mkdir()
        link=self.store.root/str(lease.job_id);link.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(OSError):self.output(lease)
        self.assertEqual(list(outside.iterdir()),[]);link.unlink()
        with patch('scriptalocal.job_artifacts.os.link',side_effect=OSError('simulated storage failure')):
            with self.assertRaises(OSError):self.output(lease)
        self.assertFalse(list(self.store.root.rglob('*.tmp')))
        self.assertFalse(list(self.store.root.rglob('*.json')))
        self.assertEqual(self.queue.get(lease.job_id,'alice')['status'],'running')

    def test_final_publication_and_cancellation_race_preserves_one_terminal_outcome(self):
        from threading import Barrier
        job,lease=self.recognition();artifact=self.final(lease);barrier=Barrier(2)
        def finish():barrier.wait();return self.queue.complete(lease,[artifact])
        def cancel():
            barrier.wait()
            try:return self.queue.cancel(job['id'],'alice')['status']
            except QueueError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(finish);second=pool.submit(cancel);outcomes=[first.result(timeout=10),second.result(timeout=10)]
        status=self.queue.get(job['id'],'alice')['status']
        self.assertIn(status,{'succeeded','cancelled'})
        if status=='succeeded':
            self.assertIn('terminal_job',outcomes);self.assertEqual(self.queue.result_reference(job['id'],'alice').id,artifact.id)
        else:
            self.assertIn('cancelled',outcomes)
            with self.assertRaises(QueueError):self.queue.result_reference(job['id'],'alice')

    def test_invalid_settings_identity_and_failure_message_rejected(self):
        with self.assertRaises(ValueError):QueueSettings(lease_seconds=0)
        with self.assertRaises(ValueError):self.create(owner='')
        with self.assertRaises(ValueError):self.create(key='has spaces')
        self.create();lease=self.queue.claim('worker',['layout'])
        with self.assertRaises(ValueError):self.queue.fail(lease,'raw exception: private data')


if __name__=='__main__':unittest.main()
