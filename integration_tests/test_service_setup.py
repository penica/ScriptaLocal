import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.configure_service import configure
from scriptalocal.ocr_access import CredentialStore


class ServiceSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);self.model=self.root/'model';self.model.mkdir()
        manifest=b'{"license":"MIT","files":[]}'
        (self.model/'manifest.json').write_bytes(manifest);self.digest=hashlib.sha256(manifest).hexdigest()
        self.args=[self.root/'service',self.model,'sha256:'+'a'*64,'sha256:'+'b'*64,self.digest]

    def test_private_configuration_authenticates_without_storing_raw_tokens_in_settings(self):
        root=configure(*self.args,backup_destination=self.root/'backup')
        token=(root/'secrets/caller.token').read_text().strip()
        credentials=CredentialStore(root/'settings/credentials.json');caller=credentials.authenticate('Bearer '+token)
        self.assertEqual(caller.owner,'registers-integration');caller.require('write');caller.require('delete')
        self.assertNotIn(token,(root/'settings/credentials.json').read_text())
        self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((root/'secrets').stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((root/'secrets/db-dsn').stat().st_mode),0o600)
        health=credentials.authenticate('Bearer '+(root/'secrets/health.token').read_text().strip())
        self.assertEqual(health.capabilities,frozenset({'read'}))
        config=json.loads((root/'settings/worker.json').read_text());self.assertEqual(config['runtime_image'],self.args[3]);self.assertEqual(config['license'],'MIT')

    def test_second_configuration_cannot_rotate_or_overwrite_existing_credentials(self):
        root=configure(*self.args);before=(root/'secrets/caller.token').read_bytes()
        with self.assertRaises(FileExistsError):configure(*self.args)
        self.assertEqual((root/'secrets/caller.token').read_bytes(),before)

    def test_floating_images_and_mismatched_models_are_rejected_before_state_creation(self):
        for position,value in [(2,'latest'),(3,'example:latest'),(4,'0'*64)]:
            args=list(self.args);args[position]=value
            with self.subTest(position=position),self.assertRaises(ValueError):configure(*args)
            self.assertFalse(self.args[0].exists())
