from pathlib import Path
import stat
import tempfile
import unittest

from scripts.configure_lan import configure


class LANSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);(self.root/'compose.env').write_text('base-configured=true\n');(self.root/'secrets').mkdir()
        self.token=self.root/'source.token';self.token.write_text('a'*40);self.image='sha256:'+'b'*64

    def test_dns_challenge_uses_protected_file_and_config_is_not_replaced(self):
        report=configure(self.root,'scripta.penic.net','10.10.100.209',self.image,self.token)
        config=(self.root/'lan/Caddyfile').read_text()
        self.assertNotIn('a'*40,config);self.assertIn('{file./run/secrets/cloudflare_api_token}',config)
        self.assertIn('acme-v02.api.letsencrypt.org',config)
        self.assertEqual(stat.S_IMODE((self.root/'secrets/cloudflare-api-token').stat().st_mode),0o600)
        self.assertEqual(report['origin'],'https://scripta.penic.net:8093')
        with self.assertRaises(FileExistsError):configure(self.root,'other.penic.net','10.10.100.209',self.image,self.token)
        self.assertEqual((self.root/'lan/Caddyfile').read_text(),config)

    def test_invalid_addresses_hostnames_and_images_do_not_create_lan_state(self):
        for host,address,image in [('scripta.penic.net','0.0.0.0',self.image),('scripta.penic.net','8.8.8.8',self.image),('bad\nname','10.10.100.209',self.image),('scripta.penic.net','10.10.100.209','caddy:latest')]:
            with self.subTest(host=host,address=address),self.assertRaises(ValueError):configure(self.root,host,address,image,self.token)
            self.assertFalse((self.root/'lan').exists())
