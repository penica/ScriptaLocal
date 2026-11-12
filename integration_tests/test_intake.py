import asyncio
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import quote

import httpx
from fastapi.testclient import TestClient

from scriptalocal.intake import create_app
from scriptalocal.intake_store import IntakeSettings, IntakeStore


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.destination=self.root/'handover'
        self.settings=IntakeSettings(state_root=self.root/'state',destinations={'handover':self.destination},
                                     admin_token='test-administrator-'+('a'*32),maximum_file_bytes=100,
                                     maximum_total_bytes=200,maximum_concurrent_uploads=1)
        self.app=create_app(self.settings);self.client=TestClient(self.app)
        self.client.__enter__()
        self.admin={'Authorization':'Bearer '+self.settings.admin_token}

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temporary.cleanup()

    def ticket(self,**overrides):
        options={'destination':'handover','label':'Contract handover','max_files':2,
                 'max_file_bytes':8,'max_total_bytes':10,'expires_in_seconds':3600,
                 'allowed_suffixes':['.json','.md']};options.update(overrides)
        response=self.client.post('/intake/v1/requests',json=options,headers=self.admin)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def headers(self,ticket,name):
        return {'Authorization':'Bearer '+ticket['upload_token'],'X-Upload-Filename':quote(name,safe=''),
                'Content-Type':'application/octet-stream'}

    def upload(self,ticket,name,content):
        return self.client.put('/intake/v1/requests/'+ticket['request_id']+'/files',
                               content=content,headers=self.headers(ticket,name))

    def test_request_upload_and_receipt_scoped_to_requested_folder(self):
        ticket=self.ticket(subfolder='api-contract-01')
        response=self.upload(ticket,'contract.json',b'{"v":1}')
        self.assertEqual(response.status_code,201,response.text)
        receipt=response.json()
        self.assertEqual((self.destination/'api-contract-01/contract.json').read_bytes(),b'{"v":1}')
        self.assertEqual(receipt['sha256'],hashlib.sha256(b'{"v":1}').hexdigest())
        self.assertEqual(receipt['remaining_files'],1)
        self.assertNotIn(str(self.root),response.text)
        self.assertNotIn(ticket['upload_token'],response.text)
        self.assertEqual((self.destination/'api-contract-01/contract.json').stat().st_mode&0o777,0o600)

    def test_administrator_and_upload_capabilities_are_separate(self):
        self.assertEqual(self.client.post('/intake/v1/requests',json={}).status_code,401)
        ticket=self.ticket();other=self.ticket(subfolder='other')
        headers=self.headers(ticket,'file.md')
        bad=self.client.put('/intake/v1/requests/'+other['request_id']+'/files',content=b'x',headers=headers)
        self.assertEqual(bad.status_code,404)
        self.assertEqual(self.client.get('/intake/v1/destinations',headers=headers).status_code,401)
        self.assertEqual(self.client.post('/intake/v1/requests/'+ticket['request_id']+'/revoke',headers=headers).status_code,401)
        with self.app.state.store.connection() as db:
            stored=db.execute('SELECT token_hash FROM requests WHERE id=?',(ticket['request_id'],)).fetchone()[0]
        self.assertNotEqual(stored,ticket['upload_token'])
        self.assertEqual(stored,hashlib.sha256(ticket['upload_token'].encode()).hexdigest())

    def test_retries_are_idempotent_and_different_content_never_overwrites(self):
        ticket=self.ticket(max_files=1)
        self.assertEqual(self.upload(ticket,'file.md',b'first').status_code,201)
        replay=self.upload(ticket,'file.md',b'first')
        self.assertEqual(replay.status_code,200,replay.text);self.assertTrue(replay.json()['replayed'])
        self.assertEqual(replay.json()['remaining_files'],0)
        self.assertEqual(self.upload(ticket,'file.md',b'other').status_code,409)
        self.assertEqual(self.upload(ticket,'second.md',b'x').status_code,409)
        self.assertEqual((self.destination/'file.md').read_bytes(),b'first')

    def test_byte_total_count_and_extension_limits(self):
        ticket=self.ticket()
        self.assertEqual(self.upload(ticket,'file.md',b'123456789').status_code,413)
        self.assertEqual(self.upload(ticket,'file.exe',b'x').status_code,415)
        self.assertEqual(self.upload(ticket,'a.md',b'12345').status_code,201)
        self.assertEqual(self.upload(ticket,'b.md',b'123456').status_code,413)
        self.assertFalse((self.destination/'b.md').exists())
        self.assertEqual(self.upload(ticket,'b.md',b'12345').status_code,201)
        self.assertFalse(list(self.destination.glob('.intake-*.part')))

    def test_filename_and_subfolder_traversal_rejected(self):
        ticket=self.ticket()
        for filename in ['../outside.md','/tmp/outside.md','a\\b.md','.env','x\x00.md','a%2f.md']:
            with self.subTest(filename=filename):
                self.assertEqual(self.upload(ticket,filename,b'x').status_code,422)
        for subfolder in ['../state','/tmp','a/../b','a\\b','.private']:
            with self.subTest(subfolder=subfolder):
                result=self.client.post('/intake/v1/requests',headers=self.admin,
                    json={'destination':'handover','label':'test','subfolder':subfolder})
                self.assertEqual(result.status_code,422)

    def test_symlink_destinations_and_existing_symlink_files_rejected(self):
        ticket=self.ticket();external=self.root/'outside';external.mkdir()
        (external/'external.md').write_bytes(b'secret')
        (self.destination/'evil.md').symlink_to(external/'external.md')
        self.assertEqual(self.upload(ticket,'evil.md',b'other').status_code,409)
        self.assertEqual((external/'external.md').read_bytes(),b'secret')
        (self.destination/'bad-folder').symlink_to(external,target_is_directory=True)
        response=self.client.post('/intake/v1/requests',headers=self.admin,
             json={'destination':'handover','subfolder':'bad-folder','label':'test',
                   'max_file_bytes':8,'max_total_bytes':10})
        self.assertEqual(response.status_code,503)
        self.assertNotIn(str(external),response.text)

    def test_revocation_and_expiry_rechecked_before_publication(self):
        ticket=self.ticket();identifier=ticket['request_id']
        self.assertEqual(self.client.post('/intake/v1/requests/'+identifier+'/revoke',headers=self.admin).status_code,200)
        self.assertEqual(self.upload(ticket,'file.md',b'x').status_code,410)
        expired=self.ticket(subfolder='expired')
        with self.app.state.store.transaction() as db:db.execute('UPDATE requests SET expires=0 WHERE id=?',(expired['request_id'],))
        self.assertEqual(self.upload(expired,'file.md',b'x').status_code,410)
        self.assertFalse((self.destination/'expired/file.md').exists())

    def test_restart_recovers_interrupted_upload_and_retains_completed_receipts(self):
        ticket=self.ticket()
        self.assertEqual(self.upload(ticket,'complete.md',b'ok').status_code,201)
        store=self.app.state.store;row=store.request(ticket['request_id'],ticket['upload_token'])
        reservation,_=store.reserve(row,'partial.md')
        part=self.destination/('.intake-'+reservation['id']+'.part');part.write_bytes(b'part')
        self.client.__exit__(None,None,None)
        self.app=create_app(self.settings);self.client=TestClient(self.app);self.client.__enter__()
        self.assertFalse(part.exists())
        self.assertEqual(self.upload(ticket,'complete.md',b'ok').status_code,200)
        self.assertEqual(self.upload(ticket,'partial.md',b'new').status_code,201)

    def test_replay_detects_missing_or_changed_files(self):
        ticket=self.ticket();self.upload(ticket,'file.md',b'first')
        (self.destination/'file.md').write_bytes(b'other')
        self.assertEqual(self.upload(ticket,'file.md',b'first').status_code,409)
        (self.destination/'file.md').unlink()
        self.assertEqual(self.upload(ticket,'file.md',b'first').status_code,409)

    def test_chunked_oversize_upload_cannot_bypass_limits(self):
        ticket=self.ticket()
        async def run():
            async def body():yield b'1234';yield b'56789'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                return await client.put('/intake/v1/requests/'+ticket['request_id']+'/files',
                   content=body(),headers=self.headers(ticket,'chunked.md'))
        response=asyncio.run(run());self.assertEqual(response.status_code,413,response.text)
        self.assertFalse((self.destination/'chunked.md').exists())
        self.assertEqual(self.app.state.store.describe(ticket['request_id'])['remaining_files'],2)
        self.assertFalse(list(self.destination.glob('.intake-*.part')))

    def test_concurrent_uploads_respect_capacity_without_duplicate_publication(self):
        ticket=self.ticket(max_files=1)
        async def run():
            async def body():yield b'a';await asyncio.sleep(.05);yield b'b'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                async def upload(name):return await client.put('/intake/v1/requests/'+ticket['request_id']+'/files',
                    content=body(),headers=self.headers(ticket,name))
                return await asyncio.gather(upload('one.md'),upload('two.md'))
        responses=asyncio.run(run());self.assertEqual(sorted(x.status_code for x in responses),[201,429])
        self.assertEqual(len(list(self.destination.glob('*.md'))),1)
        self.assertEqual(self.app.state.active_uploads,0)

    def test_single_process_lock_and_invalid_configuration(self):
        with self.assertRaises(RuntimeError):IntakeStore(self.settings)
        with self.assertRaises(ValueError):replace(self.settings,admin_token='short')
        with self.assertRaises(ValueError):replace(self.settings,destinations={'state':self.settings.state_root})

    def test_concurrent_reservations_cannot_exceed_request_file_count(self):
        self.client.__exit__(None,None,None)
        self.app=create_app(replace(self.settings,maximum_concurrent_uploads=4))
        self.client=TestClient(self.app);self.client.__enter__()
        ticket=self.ticket(max_files=1)
        async def run():
            async def body():yield b'a';await asyncio.sleep(.05);yield b'b'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                async def upload(name):return await client.put('/intake/v1/requests/'+ticket['request_id']+'/files',
                    content=body(),headers=self.headers(ticket,name))
                return await asyncio.gather(upload('one.md'),upload('two.md'))
        responses=asyncio.run(run())
        self.assertEqual(sorted(response.status_code for response in responses),[201,409])
        self.assertEqual(len(self.app.state.store.describe(ticket['request_id'])['files']),1)

    def test_revocation_during_stream_prevents_file_publication(self):
        ticket=self.ticket()
        async def run():
            async def body():
                yield b'a'
                self.app.state.store.revoke(ticket['request_id'])
                yield b'b'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                return await client.put('/intake/v1/requests/'+ticket['request_id']+'/files',
                    content=body(),headers=self.headers(ticket,'revoked.md'))
        self.assertEqual(asyncio.run(run()).status_code,410)
        self.assertFalse((self.destination/'revoked.md').exists())
        self.assertFalse(list(self.destination.glob('.intake-*.part')))

    def test_unauthorized_request_does_not_consume_upload_body(self):
        ticket=self.ticket();consumed=[]
        async def run():
            async def body():consumed.append(True);yield b'data'
            headers=self.headers(ticket,'file.md');headers['Authorization']='Bearer wrong'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                return await client.put('/intake/v1/requests/'+ticket['request_id']+'/files',content=body(),headers=headers)
        self.assertEqual(asyncio.run(run()).status_code,404)
        self.assertFalse(consumed)

    def test_upload_timeout_releases_reservation_and_temporary_file(self):
        self.client.__exit__(None,None,None)
        self.app=create_app(replace(self.settings,upload_timeout_seconds=1))
        self.client=TestClient(self.app);self.client.__enter__();ticket=self.ticket()
        async def run():
            async def body():yield b'a';await asyncio.sleep(2);yield b'b'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                return await client.put('/intake/v1/requests/'+ticket['request_id']+'/files',
                    content=body(),headers=self.headers(ticket,'timeout.md'))
        self.assertEqual(asyncio.run(run()).status_code,408)
        self.assertFalse((self.destination/'timeout.md').exists())
        self.assertEqual(self.app.state.store.describe(ticket['request_id'])['remaining_files'],2)
        self.assertEqual(self.app.state.active_uploads,0)

    def test_openapi_documents_scopes_and_required_upload_headers(self):
        self.assertEqual(self.client.get('/intake/v1/openapi.json').status_code,401)
        first=self.client.get('/intake/v1/openapi.json',headers=self.admin).json()
        second=self.client.get('/intake/v1/openapi.json',headers=self.admin).json()
        self.assertEqual(first,second)
        operation=first['paths']['/intake/v1/requests/{request_id}/files']['put']
        self.assertEqual(operation['security'],[{'UploadTicket':[]}])
        self.assertEqual(len([p for p in operation['parameters'] if p['name']=='X-Upload-Filename']),1)

    def test_existing_fifo_is_rejected_without_blocking_the_server(self):
        ticket=self.ticket();os.mkfifo(self.destination/'pipe.md')
        self.assertEqual(self.upload(ticket,'pipe.md',b'data').status_code,409)

    def download_ticket(self,**changes):
        self.destination.mkdir(exist_ok=True)
        (self.destination/'response.md').write_bytes(b'contract response')
        options={'destination':'handover','label':'Response','filenames':['response.md'],'expires_in_seconds':3600}
        options.update(changes)
        result=self.client.post('/intake/v1/downloads',headers=self.admin,json=options)
        self.assertEqual(result.status_code,201,result.text)
        ticket=result.json()
        return ticket,'/intake/v1/downloads/'+ticket['request_id'],{'Authorization':'Bearer '+ticket['download_token']}

    def test_download_snapshot_manifest_checksum_and_no_upload_capability(self):
        ticket,url,headers=self.download_ticket()
        manifest=self.client.get(url,headers=headers)
        self.assertEqual(manifest.status_code,200)
        self.assertNotIn(ticket['download_token'],manifest.text)
        self.assertNotIn(str(self.root),manifest.text)
        item=ticket['files'][0];path=url+'/files/'+item['file_id']
        response=self.client.get(path,headers=headers)
        self.assertEqual(response.content,b'contract response')
        self.assertEqual(response.headers['x-content-sha256'],hashlib.sha256(response.content).hexdigest())
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.get(path,headers=self.admin).status_code,404)
        self.assertEqual(self.client.post('/intake/v1/requests',headers=headers,json={}).status_code,401)
        self.assertEqual(self.client.put('/intake/v1/requests/'+ticket['request_id']+'/files',headers=headers,content=b'x').status_code,404)
        (self.destination/'response.md').write_bytes(b'changed source')
        self.assertEqual(self.client.get(path,headers=headers).content,b'contract response')

    def test_download_expiry_revocation_and_ticket_isolation(self):
        ticket,url,headers=self.download_ticket()
        other,other_url,other_headers=self.download_ticket()
        self.assertEqual(self.client.get(other_url,headers=headers).status_code,404)
        upload=self.ticket()
        self.assertEqual(self.client.get(url,headers=self.headers(upload,'x.md')).status_code,404)
        self.assertEqual(self.client.post(url+'/revoke',headers=headers).status_code,401)
        self.assertEqual(self.client.post(url+'/revoke',headers=self.admin).status_code,200)
        self.assertEqual(self.client.get(url,headers=headers).status_code,410)
        self.assertEqual(self.client.get(url+'/files/'+ticket['files'][0]['file_id'],headers=headers).status_code,410)
        with self.app.state.store.transaction() as db:
            db.execute('UPDATE downloads SET expires=0 WHERE id=?',(other['request_id'],))
        self.assertEqual(self.client.get(other_url,headers=other_headers).status_code,410)

    def test_download_rejects_paths_symlinks_fifo_limits_and_tampering(self):
        ticket,url,headers=self.download_ticket()
        for name in ['../response.md','missing.md']:
            response=self.client.post('/intake/v1/downloads',headers=self.admin,json={'destination':'handover','label':'bad','filenames':[name]})
            self.assertIn(response.status_code,[422,503])
        (self.destination/'link.md').symlink_to(self.destination/'response.md')
        os.mkfifo(self.destination/'pipe.md')
        (self.destination/'large.md').write_bytes(b'x'*101)
        for name in ['link.md','pipe.md','large.md']:
            response=self.client.post('/intake/v1/downloads',headers=self.admin,json={'destination':'handover','label':'bad','filenames':[name]})
            self.assertIn(response.status_code,[422,503])
        target=self.settings.state_root/'downloads'/ticket['request_id']/ticket['files'][0]['file_id']
        target.write_bytes(b'X'*len(b'contract response'))
        self.assertEqual(self.client.get(url+'/files/'+ticket['files'][0]['file_id'],headers=headers).status_code,409)
        target.unlink();target.symlink_to(self.destination/'response.md')
        self.assertEqual(self.client.get(url+'/files/'+ticket['files'][0]['file_id'],headers=headers).status_code,503)
        self.assertEqual(len(list((self.settings.state_root/'downloads').iterdir())),1)

    def test_download_persists_after_service_restart(self):
        ticket,url,headers=self.download_ticket()
        self.client.__exit__(None,None,None)
        self.app=create_app(self.settings);self.client=TestClient(self.app);self.client.__enter__()
        response=self.client.get(url+'/files/'+ticket['files'][0]['file_id'],headers=headers)
        self.assertEqual(response.content,b'contract response')
        with self.app.state.store.connection() as db:
            row=db.execute('SELECT * FROM downloads WHERE id=?',(ticket['request_id'],)).fetchone()
        self.assertNotIn(ticket['download_token'],str(dict(row)))



if __name__=='__main__':unittest.main()
