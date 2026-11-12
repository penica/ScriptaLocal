import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scriptalocal.worker_readiness import ResidentReadiness
from scriptalocal.job_queue import canonical_hash
from scriptalocal.ocr_access import ProfileRegistry


class ReadinessTests(unittest.TestCase):
    def test_registry_accepts_live_publisher_and_rejects_stale_or_wrong_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'ready.json';profile=root/'profiles.json'
            manifest={'models':{'recognition':{'engine':'transformers','model_id':'local/test','revision':'a'*40,'weights_sha256':'b'*64}}}
            checksum=canonical_hash(manifest)
            profile.write_text(json.dumps({'default_model':'local/test','profiles':[{'configuration':manifest,'license':'MIT','scripts':['Kurrent'],'ready_file':str(marker)}]}))
            registry=ProfileRegistry(profile)
            with ResidentReadiness(marker,checksum):
                self.assertEqual(registry.select(None).configuration_sha256,checksum)
            self.assertEqual(registry.snapshot()[1][0].readiness,'unavailable')
            for age,digest in [(120,checksum),(-120,checksum),(0,'c'*64)]:
                marker.write_text(json.dumps({'last_seen':time.time()-age,'state':'ready','configuration_sha256':digest}))
                self.assertEqual(registry.snapshot()[1][0].readiness,'unavailable')

    def test_marker_refreshes_and_is_removed_on_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ready.json'
            with ResidentReadiness(path,'a'*64,interval_seconds=.01):
                first=json.loads(path.read_text());deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    current=json.loads(path.read_text())
                    if current['last_seen']>first['last_seen']:break
                    time.sleep(.005)
                self.assertGreater(current['last_seen'],first['last_seen'])
                self.assertEqual(current['configuration_sha256'],'a'*64);self.assertEqual(current['state'],'ready')
            self.assertFalse(path.exists());self.assertTrue(Path(str(path)+'.lock').exists())

    def test_duplicate_publisher_cannot_replace_or_remove_live_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ready.json'
            with ResidentReadiness(path,'a'*64):
                with self.assertRaises(BlockingIOError):
                    with ResidentReadiness(path,'b'*64):self.fail('Duplicate readiness lock admitted')
                self.assertEqual(json.loads(path.read_text())['configuration_sha256'],'a'*64)

    def test_failed_refresh_becomes_unhealthy_and_preserves_atomic_last_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ready.json'
            with ResidentReadiness(path,'a'*64,interval_seconds=.01) as marker:
                with patch('scriptalocal.worker_readiness.atomic_json',side_effect=OSError('private detail')):
                    deadline=time.monotonic()+2
                    while not marker.failed and time.monotonic()<deadline:time.sleep(.005)
                    with self.assertRaisesRegex(RuntimeError,'Readiness publication failed'):marker.check()
                self.assertEqual(json.loads(path.read_text())['state'],'ready')
            self.assertFalse(path.exists())

    def test_invalid_intervals_are_rejected(self):
        for value in [0,-1,True,float('nan'),float('inf'),'5']:
            with self.subTest(value=value),self.assertRaises(ValueError):ResidentReadiness('/unused','a'*64,interval_seconds=value)
