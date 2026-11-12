from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import time
import unittest
from unittest.mock import patch

import psycopg
from integration_tests import test_jobs as fixtures
from scriptalocal.job_artifacts import ArtifactError
from scriptalocal.job_queue import JobQueue,QueueError,QueueSettings,LeaseLost
from scriptalocal.worker import StageWorker,StageFailure,WorkerUnavailable


class WorkerTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.JobQueueTests.setUpClass.__func__)
    setUp=fixtures.JobQueueTests.setUp
    tearDown=fixtures.JobQueueTests.tearDown
    clear=fixtures.JobQueueTests.clear
    create=fixtures.JobQueueTests.create
    expire=fixtures.JobQueueTests.expire
    output=fixtures.JobQueueTests.output

    def worker(self,handler,**kwargs):
        return StageWorker(self.queue,'worker-under-test',{'layout':handler},**kwargs)

    def test_handler_publication_advances_once_and_passes_committed_artifacts(self):
        job=self.create()
        def layout(context):
            self.assertEqual(context.inputs(),())
            return [self.output(context.lease)]
        result=self.worker(layout).run_once();self.assertEqual(result.status,'completed')
        lease=self.queue.claim('segmentation',['segmentation'])
        records=self.queue.input_artifacts(lease)
        self.assertEqual(len(records),1);self.assertEqual(records[0].logical_key,'output')
        self.assertEqual(json.loads(self.store.read(records[0])),{'stage':'layout'})
        self.assertEqual(self.worker(layout).run_once().status,'idle')
        self.queue.cancel(job['id'],'alice')
        with self.assertRaises(QueueError):self.queue.input_artifacts(lease)

    def test_background_heartbeats_keep_long_stage_lease_alive(self):
        self.queue=JobQueue(self.dsn,self.store,settings=QueueSettings(lease_seconds=1,retry_delay_seconds=0))
        job=self.create()
        def layout(context):
            threading.Event().wait(1.35)
            self.assertEqual(self.queue.recover_expired(),0)
            context.checkpoint()
            yield self.output(context.lease)
        result=self.worker(layout,heartbeat_interval=.1).run_once()
        self.assertEqual(result.status,'completed');self.assertEqual(self.queue.get(job['id'],'alice')['status'],'queued')

    def test_running_cancellation_stops_handler_and_never_publishes(self):
        job=self.create();entered=threading.Event()
        def layout(context):
            entered.set()
            until=time.monotonic()+3
            while time.monotonic()<until:
                context.checkpoint();threading.Event().wait(.01)
            self.fail('Cancellation checkpoint was not observed')
        worker=self.worker(layout,heartbeat_interval=.02)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(worker.run_once);self.assertTrue(entered.wait(2))
            self.queue.cancel(job['id'],'alice');result=pending.result(timeout=5)
        self.assertEqual(result.status,'cancelled')
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'cancelled')
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)

    def test_expired_old_worker_cannot_publish_after_reclaim(self):
        job=self.create();entered=threading.Event();release=threading.Event();old=[]
        def layout(context):
            old.append(context.lease);entered.set();self.assertTrue(release.wait(3))
            # A handler ignoring checkpoints still cannot publish its output.
            return [self.output(context.lease)]
        worker=self.worker(layout,heartbeat_interval=.05)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(worker.run_once);self.assertTrue(entered.wait(2))
            self.expire(old[0]);self.assertEqual(self.queue.recover_expired(),1)
            new=self.queue.claim('replacement',['layout']);self.assertIsNotNone(new)
            self.assertNotEqual(old[0].token,new.token)
            release.set();result=pending.result(timeout=5)
        self.assertEqual(result.status,'lease_lost')
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)
        self.queue.cancel(job['id'],'alice');self.queue.acknowledge_cancel(new)

    def test_heartbeat_failure_is_fail_closed_and_worker_requires_restart(self):
        job=self.create();handler_called=[]
        worker=self.worker(lambda context:handler_called.append(True))
        with patch.object(self.queue,'heartbeat',side_effect=psycopg.OperationalError('private outage details')):
            result=worker.run_once()
        self.assertEqual(result.status,'unavailable');self.assertFalse(handler_called)
        self.assertFalse(worker.healthy)
        with self.assertRaises(WorkerUnavailable):worker.run_once()
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'running')

    def test_midstage_heartbeat_failure_interrupts_processing_without_publication(self):
        job=self.create();pulse=self.queue.heartbeat;calls=[]
        def unavailable_after_start(lease):
            calls.append(True)
            if len(calls)>1:raise psycopg.OperationalError('private outage')
            return pulse(lease)
        def layout(context):
            until=time.monotonic()+2
            while time.monotonic()<until:
                context.checkpoint();threading.Event().wait(.01)
            self.fail('Heartbeat outage did not interrupt handler')
        worker=self.worker(layout,heartbeat_interval=.02)
        with patch.object(self.queue,'heartbeat',side_effect=unavailable_after_start):result=worker.run_once()
        self.assertEqual(result.status,'unavailable');self.assertFalse(worker.healthy)
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'running')
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)

    def test_stalled_heartbeat_drain_never_permits_artifact_publication(self):
        self.create();pulse=self.queue.heartbeat;calls=[]
        blocked=threading.Event();release=threading.Event();finished=threading.Event()
        def delayed(lease):
            calls.append(True)
            if len(calls)>1:
                blocked.set()
                try:
                    if not release.wait(3):raise RuntimeError('test synchronization timeout')
                    return pulse(lease)
                finally:finished.set()
            return pulse(lease)
        def layout(context):
            self.assertTrue(blocked.wait(2))
            return [self.output(context.lease)]
        worker=self.worker(layout,heartbeat_interval=.02,heartbeat_join_seconds=.02)
        with patch.object(self.queue,'heartbeat',side_effect=delayed):
            try:
                result=worker.run_once()
                self.assertEqual(result.status,'unavailable');self.assertFalse(worker.healthy)
                with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)
            finally:release.set();self.assertTrue(finished.wait(2))
        with self.assertRaises(WorkerUnavailable):worker.run_once()

    def test_retryable_failure_requeues_with_attempt_bound(self):
        self.queue=JobQueue(self.dsn,self.store,settings=QueueSettings(maximum_attempts=2,retry_delay_seconds=0))
        job=self.create()
        def fail(context):raise StageFailure('temporary_model_failure',retryable=True)
        worker=self.worker(fail)
        self.assertEqual(worker.run_once().status,'requeued')
        self.assertEqual(worker.run_once().status,'failed')
        self.assertEqual(worker.run_once().status,'idle')
        row=self.queue.get(job['id'],'alice');self.assertEqual(row['error_code'],'temporary_model_failure')

    def test_unexpected_handler_failure_is_redacted_and_terminal(self):
        job=self.create()
        def fail(context):raise RuntimeError('PRIVATE MODEL OR MANUSCRIPT DETAILS')
        result=self.worker(fail).run_once()
        self.assertEqual((result.status,result.error_code),('failed','stage_failed'))
        row=self.queue.get(job['id'],'alice');self.assertEqual(row['error_code'],'stage_failed')
        self.assertNotIn('PRIVATE',str(result))

    def test_invalid_completion_artifact_fails_job_instead_of_stalling(self):
        job=self.create()
        def invalid(context):return [self.store.publish(context.lease,'output',b'not JSON')]
        result=self.worker(invalid).run_once()
        self.assertEqual((result.status,result.error_code),('failed','invalid_stage_artifact'))
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'failed')

    def test_configuration_routing_does_not_consume_other_profile_jobs(self):
        job=self.create();worker=self.worker(lambda context:[self.output(context.lease)],configuration_sha256=('1'*64 if self.configuration!='1'*64 else '2'*64))
        self.assertEqual(worker.run_once().status,'idle')
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'queued')
        worker=self.worker(lambda context:[self.output(context.lease)],configuration_sha256=self.configuration)
        self.assertEqual(worker.run_once().status,'completed')

    def test_input_refs_are_not_shared_between_jobs_or_expired_leases(self):
        first=self.create();self.worker(lambda context:[self.output(context.lease)]).run_once()
        second=self.create(owner='bob')
        layout=self.queue.claim('layout',['layout']);self.assertEqual(layout.job_id,second['id'])
        self.assertEqual(self.queue.input_artifacts(layout),())
        segmentation=self.queue.claim('segment',['segmentation']);self.assertEqual(segmentation.job_id,first['id'])
        self.expire(segmentation)
        with self.assertRaises(LeaseLost):self.queue.input_artifacts(segmentation)

    def test_ambiguous_commit_response_does_not_publish_twice(self):
        job=self.create();complete=self.queue.complete
        def lost_response(*args,**kwargs):
            complete(*args,**kwargs)
            raise psycopg.OperationalError('PRIVATE lost database response')
        worker=self.worker(lambda context:[self.output(context.lease)])
        with patch.object(self.queue,'complete',side_effect=lost_response):result=worker.run_once()
        self.assertEqual(result.status,'unavailable');self.assertFalse(worker.healthy)
        self.assertEqual(self.queue.get(job['id'],'alice')['status'],'queued')
        self.assertEqual(self.worker(lambda context:[]).run_once().status,'idle')
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],1)
            self.assertEqual(db.execute("SELECT count(*) AS count FROM sl_stages WHERE name='segmentation'").fetchone()['count'],1)

    def test_cancel_ack_database_failure_requires_worker_restart(self):
        job=self.create()
        def layout(context):
            self.queue.cancel(job['id'],'alice')
            return [self.output(context.lease)]
        worker=self.worker(layout)
        with patch.object(self.queue,'acknowledge_cancel',side_effect=psycopg.OperationalError('private outage')):
            result=worker.run_once()
        self.assertEqual(result.status,'unavailable');self.assertFalse(worker.healthy)
        row=self.queue.get(job['id'],'alice');self.assertTrue(row['cancel_requested'])
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_artifacts').fetchone()['count'],0)

    def test_database_statement_deadline_is_enforced(self):
        queue=JobQueue(self.dsn,self.store,settings=QueueSettings(database_statement_timeout_ms=50))
        with self.assertRaises(psycopg.errors.QueryCanceled):
            with queue.connect() as db:db.execute('SELECT pg_sleep(1)')

    def test_worker_interval_and_handler_settings_are_checked(self):
        for interval in [0,-1,True,float('nan'),self.queue.settings.lease_seconds]:
            with self.assertRaises(ValueError):self.worker(lambda _:[],heartbeat_interval=interval)
        with self.assertRaises(ValueError):StageWorker(self.queue,'x',{})
        with self.assertRaises(ValueError):StageWorker(self.queue,'x',{'unknown':lambda _:[]})
        with self.assertRaises(ValueError):StageFailure('private exception details')


if __name__=='__main__':unittest.main()
