import json
from pathlib import Path
import time
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from integration_tests import test_ocr_api as fixtures
from scriptalocal.ocr_api import create_app


class ManualUITests(unittest.TestCase):
    setUpClass=classmethod(fixtures.OCRAPITests.setUpClass.__func__)
    clear=fixtures.OCRAPITests.clear
    headers=fixtures.OCRAPITests.headers
    tearDown=fixtures.OCRAPITests.tearDown

    def setUp(self):
        fixtures.OCRAPITests.setUp(self);self.client.__exit__(None,None,None)
        self.operator=self.root/'operator.json';self.operator.write_text(json.dumps({'backup_destination':'/old','preserved_option':True}))
        self.app=create_app(self.queue,self.sources,self.credentials,self.profiles,settings=self.settings,operator_path=self.operator)
        self.client=TestClient(self.app,base_url='https://testserver');self.client.__enter__()

    def login(self,name='alice'):
        response=self.client.post('/ui/login',headers={'X-Scripta-UI':'1','Origin':'https://testserver'},json={'key':self.tokens[name]})
        self.assertEqual(response.status_code,200,response.text)
        return response,self.client.get('/ui/session').json()

    def test_public_page_has_no_credential_and_sessions_use_secure_cookie(self):
        page=self.client.get('/ui');self.assertEqual(page.status_code,200)
        self.assertIn("frame-ancestors 'none'",page.headers['content-security-policy'])
        self.assertNotIn(self.tokens['alice'],page.text)
        self.assertEqual(self.client.get('/ui/app.js').status_code,200)
        self.assertEqual(self.client.get('/v1/models').status_code,401)
        response,session=self.login()
        cookie=response.headers['set-cookie'].lower()
        for flag in ['httponly','secure','samesite=strict']:self.assertIn(flag,cookie)
        self.assertNotIn(self.tokens['alice'],json.dumps(session));self.assertTrue(session['csrf'])
        self.assertEqual(self.client.get('/v1/models').status_code,200)

    def test_cookie_upload_needs_csrf_and_keeps_owner_isolation(self):
        _,session=self.login()
        args={'files':{'image':('row.png',self.png,'image/png')}}
        self.assertEqual(self.client.post('/v1/jobs',**args).status_code,403)
        response=self.client.post('/v1/jobs',headers={'X-CSRF-Token':session['csrf']},**args)
        self.assertEqual(response.status_code,202,response.text)
        job=response.json()['job_id']
        self.assertEqual(self.client.get('/v1/jobs/'+job,headers=self.headers('bob')).status_code,404)
        self.assertEqual(self.client.post('/ui/logout',headers={'X-CSRF-Token':session['csrf']}).status_code,200)
        self.assertEqual(self.client.get('/v1/jobs/'+job).status_code,401)

    def test_revocation_expiry_and_cross_origin_login_fail_closed(self):
        self.assertEqual(self.client.post('/ui/login',headers={'X-Scripta-UI':'1','Origin':'https://other.test'},json={'key':self.tokens['alice']}).status_code,403)
        self.login()
        data=json.loads(self.credentials_path.read_text());data['credentials'][0]['enabled']=False;self.credentials_path.write_text(json.dumps(data))
        self.assertEqual(self.client.get('/v1/models').status_code,401)
        data['credentials'][0]['enabled']=True;self.credentials_path.write_text(json.dumps(data))
        future=time.monotonic()+28801
        with patch('scriptalocal.manual_ui.time.monotonic',return_value=future):self.assertEqual(self.client.get('/v1/models').status_code,401)

    def test_backup_destination_requires_configure_capability_and_preserves_other_settings(self):
        _,session=self.login()
        self.assertEqual(self.client.get('/ui/settings').status_code,403)
        data=json.loads(self.credentials_path.read_text());data['credentials'][0]['capabilities'].append('configure');self.credentials_path.write_text(json.dumps(data))
        self.assertTrue(self.client.get('/ui/session').json()['can_configure'])
        self.assertEqual(self.client.post('/ui/settings',json={'backup_destination':'/new'}).status_code,403)
        headers={'X-CSRF-Token':session['csrf']}
        self.assertEqual(self.client.post('/ui/settings',headers=headers,json={'backup_destination':'relative'}).status_code,422)
        self.assertEqual(self.client.post('/ui/settings',headers=headers,json={'backup_destination':'/new'}).status_code,200)
        self.assertEqual(json.loads(self.operator.read_text()),{'backup_destination':'/new','preserved_option':True})
