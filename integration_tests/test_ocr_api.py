import asyncio
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4

from fastapi.testclient import TestClient
import httpx
from PIL import Image
from psycopg.types.json import Jsonb

from integration_tests import test_jobs as fixtures
from scriptalocal.contracts import JobResult
from scriptalocal.images import ImageLimits,decode_image
from scriptalocal.job_deletions import collect_deletions
from scriptalocal.job_queue import canonical_hash
from scriptalocal.ocr_access import CredentialStore,ProfileRegistry
from scriptalocal.ocr_api import APISettings,create_app
from scriptalocal.ocr_server import application,build_app
from scriptalocal.source_store import SourceStore


class OCRAPITests(unittest.TestCase):
    setUpClass=classmethod(fixtures.JobQueueTests.setUpClass.__func__)
    clear=fixtures.JobQueueTests.clear

    def setUp(self):
        fixtures.JobQueueTests.setUp(self)
        self.root=Path(self.directory.name);self.sources=SourceStore(self.root/'originals')
        self.tokens={'alice':'a'*64,'bob':'b'*64,'reader':'c'*64,'writer':'d'*64}
        self.credentials_path=self.root/'credentials.json'
        values=[{'sha256':hashlib.sha256(token.encode()).hexdigest(),'owner':'alice' if name in {'reader','writer'} else name,
                 'capabilities':['read'] if name=='reader' else ['write'] if name=='writer' else ['read','write','delete']} for name,token in self.tokens.items()]
        self.credentials_path.write_text(json.dumps({'credentials':values}));self.credentials_path.chmod(0o600)
        self.credentials=CredentialStore(self.credentials_path)
        self.profile_path=self.root/'profiles.json';self.marker=self.root/'worker-ready.json'
        self.manifest={'models':{'recognition':self.example['provenance']['recognition_model']},'test_profile':1}
        self.configuration=canonical_hash(self.manifest)
        self.profile_path.write_text(json.dumps({'default_model':self.manifest['models']['recognition']['model_id'],'profiles':[
            {'configuration':self.manifest,'license':'MIT','scripts':['German Kurrent'],'ready_file':str(self.marker)}]}))
        self.marker.write_text(json.dumps({'state':'ready','configuration_sha256':self.configuration,'last_seen':time.time()}))
        self.profiles=ProfileRegistry(self.profile_path)
        self.settings=APISettings(minimum_free_bytes=1)
        self.app=create_app(self.queue,self.sources,self.credentials,self.profiles,settings=self.settings)
        self.client=TestClient(self.app,raise_server_exceptions=False);self.client.__enter__()
        output=BytesIO();Image.new('RGB',(self.example['source']['width'],self.example['source']['height']),'white').save(output,format='PNG');self.png=output.getvalue()

    def tearDown(self):
        self.client.__exit__(None,None,None);fixtures.JobQueueTests.tearDown(self)

    def headers(self,name='alice',key=None):
        result={'Authorization':'Bearer '+self.tokens[name]}
        if key is not None:result['Idempotency-Key']=key
        return result

    def post(self,*,owner='alice',key=None,options=None,payload=None,content_type='image/png'):
        return self.client.post('/v1/jobs',headers=self.headers(owner,key),files={'image':('ignored-client-name.png',self.png if payload is None else payload,content_type)},
                                data={} if options is None else {'options':json.dumps(options)})

    def accepted(self,**kwargs):
        response=self.post(**kwargs);self.assertEqual(response.status_code,202,response.text);return response.json()

    def finish(self,job_id):
        for name in ['layout','segmentation']:
            lease=self.queue.claim('fixture-worker',[name],configuration_sha256=self.configuration)
            self.assertEqual(str(lease.job_id),job_id)
            self.queue.complete(lease,[self.store.publish(lease,'output',b'{"fixture":true}')])
        lease=self.queue.claim('fixture-worker',['recognition'],configuration_sha256=self.configuration)
        document=json.loads(json.dumps(self.example));document.update(job_id=job_id,revision=1)
        document['source']=self.queue.get(UUID(job_id),'alice')['source'];document['provenance']['configuration_sha256']=self.configuration
        record=self.store.publish(lease,'result',json.dumps(document).encode());self.queue.complete(lease,[record]);return record

    def test_health_models_and_authentication_are_separate(self):
        self.assertEqual(self.client.get('/health/live').status_code,200)
        self.assertEqual(self.client.get('/health/ready').status_code,401)
        ready=self.client.get('/health/ready',headers=self.headers('reader'))
        self.assertTrue(ready.json()['ready'])
        inventory=self.client.get('/v1/models',headers=self.headers('reader')).json()
        self.assertEqual(inventory['models'][0]['readiness'],'ready')
        self.marker.write_text(json.dumps({'state':'warming','configuration_sha256':self.configuration,'last_seen':time.time()}))
        self.assertFalse(self.client.get('/health/ready',headers=self.headers()).json()['ready'])
        self.assertEqual(self.post().status_code,503)

    def test_file_configured_factory_preserves_limits_and_startup_error_redaction(self):
        secret=self.root/'database-secret';secret.write_text(self.dsn)
        config=self.root/'server.json'
        data={'dsn_file':str(secret),'originals_root':str(self.sources.root),'artifacts_root':str(self.store.root),
              'credentials_file':str(self.credentials_path),'profiles_file':str(self.profile_path),
              'api':{'minimum_free_bytes':1,'image':{'max_bytes':100}},'queue':{'maximum_active_jobs':2}}
        config.write_text(json.dumps(data))
        with TestClient(build_app(config)) as client:
            self.assertTrue(client.get('/health/ready',headers=self.headers()).json()['ready'])
            response=client.post('/v1/jobs',headers=self.headers(),files={'image':('test.png',self.png,'image/png')})
            self.assertEqual(response.status_code,413)
        data['dsn_file']='relative-secret';config.write_text(json.dumps(data))
        with patch.dict(os.environ,{'SCRIPTALOCAL_CONFIG':str(config)}):
            with self.assertRaisesRegex(RuntimeError,'^OCR server configuration is unavailable$') as raised:application()
        self.assertIsNone(raised.exception.__cause__);self.assertTrue(raised.exception.__suppress_context__)
        data['dsn_file']=str(secret);data['unexpected']=True;config.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'configuration keys'):build_app(config)

    def test_readiness_rejects_changed_schema_without_applying_migrations(self):
        with self.queue.connect() as db:
            original=db.execute('SELECT sha256 FROM sl_schema_migrations WHERE version=2').fetchone()['sha256']
            db.execute('UPDATE sl_schema_migrations SET sha256=%s WHERE version=2',('0'*64,))
        try:
            response=self.client.get('/health/ready',headers=self.headers()).json()
            self.assertFalse(response['ready']);self.assertEqual(response['components']['database'],'unavailable')
            with self.queue.connect() as db:
                self.assertEqual(db.execute('SELECT sha256 FROM sl_schema_migrations WHERE version=2').fetchone()['sha256'],'0'*64)
        finally:
            with self.queue.connect() as db:db.execute('UPDATE sl_schema_migrations SET sha256=%s WHERE version=2',(original,))

    def test_readiness_reports_failed_physical_cleanup(self):
        with self.queue.connect() as db:
            db.execute("INSERT INTO sl_deletions(id,job_id,owner,source_sha256,artifacts,status) VALUES(%s,%s,%s,%s,%s,'failed')",
                       (uuid4(),uuid4(),'alice','0'*64,Jsonb([])))
        response=self.client.get('/health/ready',headers=self.headers()).json()
        self.assertFalse(response['ready']);self.assertEqual(response['components']['cleanup'],'unavailable')

    def test_expired_revoked_duplicate_tokens_fail_closed(self):
        data=json.loads(self.credentials_path.read_text())
        data['credentials'][0]['enabled']=False;self.credentials_path.write_text(json.dumps(data))
        self.assertEqual(self.client.get('/v1/models',headers=self.headers()).status_code,401)
        data['credentials'][0]['enabled']=True;data['credentials'][0]['expires_at']=0
        self.credentials_path.write_text(json.dumps(data));self.assertEqual(self.post().status_code,401)
        data['credentials'][0].pop('expires_at');data['credentials'].append(data['credentials'][0])
        self.credentials_path.write_text(json.dumps(data));self.assertEqual(self.post().status_code,503)

    def test_upload_preserves_bytes_and_polls_not_ready_result(self):
        accepted=self.accepted();job_id=accepted['job_id']
        self.assertEqual(accepted['api_version'],'1');self.assertFalse(accepted['idempotency_replayed'])
        digest=hashlib.sha256(self.png).hexdigest();self.assertEqual((self.sources.root/(digest+'.original')).read_bytes(),self.png)
        response=self.client.get('/v1/jobs/'+job_id,headers=self.headers()).json()
        self.assertEqual((response['status'],response['stage']),('queued','queued'))
        self.assertIsNone(response['error_code']);self.assertFalse(response['cancel_requested']);self.assertEqual(response['warnings'],[])
        self.assertEqual(self.client.get('/v1/jobs/'+job_id+'/result',headers=self.headers()).status_code,409)

    def test_capabilities_and_owner_scope_do_not_reveal_jobs(self):
        job=self.accepted()['job_id']
        self.assertEqual(self.post(owner='reader').status_code,403)
        self.assertEqual(self.client.get('/v1/jobs/'+job,headers=self.headers('writer')).status_code,403)
        missing=self.client.get('/v1/jobs/'+str(uuid4()),headers=self.headers('bob'))
        hidden=self.client.get('/v1/jobs/'+job,headers=self.headers('bob'))
        self.assertEqual((missing.status_code,hidden.status_code),(404,404))
        self.assertEqual(missing.json()['error']['code'],hidden.json()['error']['code'])
        self.assertEqual(self.client.delete('/v1/jobs/'+job,headers=self.headers('reader')).status_code,403)

    def test_idempotency_defaults_replay_and_divider_difference_conflicts(self):
        first=self.accepted(key='same')
        second=self.accepted(key='same',options={'keep_reference':False})
        self.assertEqual(first['job_id'],second['job_id']);self.assertTrue(second['idempotency_replayed'])
        self.assertEqual(self.post(key='same',options={'corrected_dividers':[]}).status_code,409)
        other=self.accepted(owner='bob',key='same');self.assertNotEqual(first['job_id'],other['job_id'])

    def test_replay_uses_existing_configuration_when_current_profile_changes(self):
        first=self.accepted(key='stable')
        data=json.loads(self.profile_path.read_text());data['profiles'][0]['configuration']['test_profile']=2
        self.profile_path.write_text(json.dumps(data))
        self.assertEqual(self.post(key='new').status_code,503)
        replay=self.accepted(key='stable');self.assertEqual(replay['job_id'],first['job_id'])
        self.assertTrue(replay['idempotency_replayed'])

    def test_deleted_operation_race_never_implicitly_recreates_job(self):
        first=self.accepted(key='race');find=self.queue.find_idempotent
        def disappearing(owner,key):
            row=find(owner,key)
            if row is None:return None
            request=self.queue.request_deletion(row['id'],owner)
            collect_deletions(self.queue,self.sources,request_id=request)
            return row
        with patch.object(self.queue,'find_idempotent',side_effect=disappearing):response=self.post(key='race')
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.client.get('/v1/jobs/'+first['job_id'],headers=self.headers()).status_code,404)
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_jobs').fetchone()['count'],0)

    def test_full_queue_rejects_before_source_write_but_allows_replay(self):
        self.queue.settings=replace(self.queue.settings,maximum_active_jobs=1)
        first=self.accepted(key='full')
        output=BytesIO();image=Image.open(BytesIO(self.png)).convert('RGB');image.putpixel((0,0),(0,0,0));image.save(output,format='PNG')
        other=output.getvalue();digest=hashlib.sha256(other).hexdigest()
        self.assertEqual(self.post(payload=other,key='new').status_code,429)
        self.assertFalse((self.sources.root/(digest+'.original')).exists())
        replay=self.accepted(key='full');self.assertEqual(replay['job_id'],first['job_id'])

    def test_source_collision_and_tampering_fail_without_new_job(self):
        image=decode_image(self.png);target=self.sources.root/(image.sha256+'.original');os.mkfifo(target)
        self.assertEqual(self.post().status_code,503)
        target.unlink();target.write_bytes(b'changed')
        self.assertEqual(self.post().status_code,503)
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_jobs').fetchone()['count'],0)

    def test_input_formats_options_and_declared_mime_are_validated(self):
        self.assertEqual(self.client.post('/v1/jobs',headers=self.headers(),json={'image':'base64'}).status_code,415)
        self.assertEqual(self.post(options={'unknown':True}).status_code,422)
        self.assertEqual(self.post(options={'keep_reference':'false'}).status_code,422)
        self.assertEqual(self.post(payload=b'not an image').status_code,422)
        self.assertEqual(self.post(content_type='image/jpeg').status_code,415)
        self.assertEqual(self.post(options={'model_id':'not-installed'}).status_code,503)
        response=self.client.post('/v1/jobs',headers=self.headers(),files=[('image',('r.png',self.png,'image/png')),('options',(None,'{}')),('options',(None,'{}'))])
        self.assertEqual(response.status_code,422)

    def test_file_pixel_and_chunked_body_limits(self):
        app=create_app(self.queue,self.sources,self.credentials,self.profiles,settings=replace(self.settings,image=ImageLimits(max_bytes=50)))
        with TestClient(app,raise_server_exceptions=False) as client:
            response=client.post('/v1/jobs',headers=self.headers(),files={'image':('r.png',self.png,'image/png')})
            self.assertEqual(response.status_code,413,response.text)
        app=create_app(self.queue,self.sources,self.credentials,self.profiles,settings=replace(self.settings,image=ImageLimits(max_pixels=10)))
        with TestClient(app,raise_server_exceptions=False) as client:
            self.assertEqual(client.post('/v1/jobs',headers=self.headers(),files={'image':('r.png',self.png,'image/png')}).status_code,413)
        app=create_app(self.queue,self.sources,self.credentials,self.profiles,settings=replace(self.settings,image=ImageLimits(max_bytes=10),maximum_options_bytes=10,multipart_overhead_bytes=10))
        with TestClient(app,raise_server_exceptions=False) as client:
            self.assertEqual(client.post('/v1/jobs',headers={**self.headers(),'Content-Type':'multipart/form-data; boundary=b'},content=iter([b'a'*20,b'b'*20])).status_code,413)

    def test_real_streaming_upload_concurrency_is_bounded(self):
        async def scenario():
            entered=asyncio.Event();release=asyncio.Event()
            body=b'--b\r\nContent-Disposition: form-data; name="image"; filename="r.png"\r\nContent-Type: image/png\r\n\r\n'+self.png+b'\r\n--b--\r\n'
            async def streaming():
                yield body[:20];entered.set();await release.wait();yield body[20:]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                pending=asyncio.create_task(client.post('/v1/jobs',headers={**self.headers(),'Content-Type':'multipart/form-data; boundary=b'},content=streaming()))
                await asyncio.wait_for(entered.wait(),2)
                busy=await client.post('/v1/jobs',headers=self.headers(),files={'image':('r.png',self.png,'image/png')})
                self.assertEqual(busy.status_code,429)
                release.set();response=await asyncio.wait_for(pending,3);self.assertEqual(response.status_code,202,response.text)
        asyncio.run(scenario())

    def test_revoked_slow_upload_cannot_admit_a_job(self):
        async def scenario():
            entered=asyncio.Event();release=asyncio.Event()
            body=b'--b\r\nContent-Disposition: form-data; name="image"; filename="r.png"\r\nContent-Type: image/png\r\n\r\n'+self.png+b'\r\n--b--\r\n'
            async def streaming():
                yield body[:20];entered.set();await release.wait();yield body[20:]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                pending=asyncio.create_task(client.post('/v1/jobs',headers={**self.headers(),'Content-Type':'multipart/form-data; boundary=b'},content=streaming()))
                await asyncio.wait_for(entered.wait(),2)
                data=json.loads(self.credentials_path.read_text());data['credentials'][0]['enabled']=False
                self.credentials_path.write_text(json.dumps(data));release.set()
                response=await asyncio.wait_for(pending,3);self.assertEqual(response.status_code,401,response.text)
        asyncio.run(scenario())
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_jobs').fetchone()['count'],0)
        self.assertFalse(list(self.sources.root.glob('*.original')))

    def test_completed_result_artifact_scope_and_terminal_cancel(self):
        job=self.accepted()['job_id'];record=self.finish(job)
        status=self.client.get('/v1/jobs/'+job,headers=self.headers()).json();self.assertEqual((status['status'],status['stage']),('succeeded','complete'))
        response=self.client.get('/v1/jobs/'+job+'/result',headers=self.headers());self.assertEqual(response.status_code,200,response.text)
        result=JobResult.model_validate_json(response.content);self.assertEqual(str(result.job_id),job)
        response=self.client.get('/v1/jobs/'+job+'/artifacts/'+str(record.id),headers=self.headers())
        self.assertEqual(response.content,self.store.read(record));self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.client.get('/v1/jobs/'+job+'/artifacts/'+str(record.id),headers=self.headers('bob')).status_code,404)
        self.assertEqual(self.client.post('/v1/jobs/'+job+'/cancel',headers=self.headers()).status_code,409)

    def test_queued_and_running_cancellation_are_idempotent_and_fenced(self):
        first=self.accepted()['job_id']
        for _ in range(2):self.assertEqual(self.client.post('/v1/jobs/'+first+'/cancel',headers=self.headers()).json()['status'],'cancelled')
        second=self.accepted()['job_id'];lease=self.queue.claim('fixture',['layout'])
        response=self.client.post('/v1/jobs/'+second+'/cancel',headers=self.headers()).json()
        self.assertEqual(response['status'],'running');self.assertTrue(response['cancel_requested'])
        self.queue.acknowledge_cancel(lease)
        self.assertEqual(self.client.get('/v1/jobs/'+second,headers=self.headers()).json()['status'],'cancelled')

    def test_foreign_mutations_leave_job_and_source_unchanged(self):
        job=self.accepted()['job_id']
        self.assertEqual(self.client.post('/v1/jobs/'+job+'/cancel',headers=self.headers('bob')).status_code,404)
        self.assertEqual(self.client.delete('/v1/jobs/'+job,headers=self.headers('bob')).status_code,404)
        self.assertEqual(self.queue.get(UUID(job),'alice')['status'],'queued')
        self.assertTrue((self.sources.root/(hashlib.sha256(self.png).hexdigest()+'.original')).exists())
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM sl_deletions').fetchone()['count'],0)

    def test_tampered_deletion_manifest_never_removes_another_jobs_file(self):
        first=self.accepted()['job_id'];self.finish(first)
        second=self.accepted()['job_id'];foreign=self.finish(second)
        request=self.queue.request_deletion(UUID(first),'alice')
        with self.queue.connect() as db:
            db.execute('UPDATE sl_deletions SET artifacts=%s WHERE id=%s',(Jsonb([foreign.manifest()]),request))
        for _ in range(3):collect_deletions(self.queue,self.sources,request_id=request)
        self.assertTrue((self.store.root/foreign.relative_path).exists())
        self.assertEqual(self.queue.get(UUID(second),'alice')['status'],'succeeded')
        with self.queue.connect() as db:
            row=db.execute('SELECT status,attempts FROM sl_deletions WHERE id=%s',(request,)).fetchone()
        self.assertEqual((row['status'],row['attempts']),('failed',3))

    def test_delete_removes_job_and_collects_unshared_original(self):
        job=self.accepted()['job_id'];digest=hashlib.sha256(self.png).hexdigest()
        self.assertTrue((self.sources.root/(digest+'.original')).exists())
        response=self.client.delete('/v1/jobs/'+job,headers=self.headers());self.assertEqual(response.status_code,204,response.text)
        self.assertEqual(self.client.get('/v1/jobs/'+job,headers=self.headers()).status_code,404)
        self.assertFalse((self.sources.root/(digest+'.original')).exists())
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT status FROM sl_deletions').fetchone()['status'],'done')

    def test_running_delete_requires_ack_and_shared_original_is_retained(self):
        first=self.accepted()['job_id'];second=self.accepted()['job_id'];digest=hashlib.sha256(self.png).hexdigest()
        lease=self.queue.claim('fixture',['layout']);self.assertEqual(str(lease.job_id),first)
        self.assertEqual(self.client.delete('/v1/jobs/'+first,headers=self.headers()).status_code,409)
        self.client.post('/v1/jobs/'+first+'/cancel',headers=self.headers());self.queue.acknowledge_cancel(lease)
        self.assertEqual(self.client.delete('/v1/jobs/'+first,headers=self.headers()).status_code,204)
        self.assertTrue((self.sources.root/(digest+'.original')).exists())
        self.assertEqual(self.client.delete('/v1/jobs/'+second,headers=self.headers()).status_code,204)
        self.assertFalse((self.sources.root/(digest+'.original')).exists())

    def test_interrupted_physical_cleanup_is_replayable_from_durable_manifest(self):
        job=self.accepted()['job_id'];record=self.finish(job)
        with patch.object(self.sources,'remove_locked',side_effect=OSError('private storage fault')):
            self.assertEqual(self.client.delete('/v1/jobs/'+job,headers=self.headers()).status_code,204)
        with self.queue.connect() as db:
            row=db.execute('SELECT status,attempts,error_code FROM sl_deletions').fetchone()
        self.assertEqual((row['status'],row['attempts']),('queued',1));self.assertNotIn('private',row['error_code'])
        self.assertFalse((self.store.root/record.relative_path).exists())
        self.assertEqual(collect_deletions(self.queue,self.sources)['done'],1)

    def test_unexpected_backend_exception_is_redacted_without_reraising(self):
        with TestClient(self.app,raise_server_exceptions=True) as client:
            with patch.object(self.queue,'connect',side_effect=RuntimeError('PRIVATE ERROR CANARY')):
                response=client.get('/v1/jobs/'+str(uuid4()),headers=self.headers())
        self.assertEqual(response.status_code,500);self.assertNotIn('PRIVATE ERROR CANARY',response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')

    def test_errors_never_echo_tokens_paths_or_uploaded_body(self):
        response=self.post(payload=b'PRIVATE OCR BODY CANARY')
        self.assertEqual(response.status_code,422)
        for secret in [self.tokens['alice'],str(self.root),'PRIVATE OCR BODY CANARY']:
            self.assertNotIn(secret,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.client.get('/v1/jobs/not-a-uuid').status_code,401)
        self.assertEqual(self.client.get('/v1/jobs/not-a-uuid',headers=self.headers()).status_code,422)


if __name__=='__main__':unittest.main()
