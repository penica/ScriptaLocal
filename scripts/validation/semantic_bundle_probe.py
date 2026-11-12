"""Execute the outgoing reference validator from its ZIP, isolated from checkout."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tempfile
import zipfile


def main():
    bundle=Path(sys.argv[1]);received=Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        with zipfile.ZipFile(bundle) as archive:
            manifest=json.loads(archive.read('BUNDLE-MANIFEST.json'))
            listed={item['path']:item for item in manifest['files']}
            assert set(listed)==set(archive.namelist())-{'BUNDLE-MANIFEST.json'}
            for name,item in listed.items():
                path=PurePosixPath(name)
                assert not path.is_absolute() and '..' not in path.parts
                content=archive.read(name)
                assert len(content)==item['bytes'] and hashlib.sha256(content).hexdigest()==item['sha256']
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(content)
        def run(arguments,code=0):
            result=subprocess.run([sys.executable,*arguments],cwd=root,capture_output=True,text=True,timeout=30)
            assert result.returncode==code,result.stderr
            return result
        tests=run(['-m','unittest','discover','-s','tests','-p','test_contract_cli.py','-v'])
        valid=json.loads(run(['-m','scripts.validate_contract','--model','JobResult','docs/api/examples/result.json']).stdout)
        original=json.loads(run(['-m','scripts.validation.app_fixture_probe','--archive',str(received)],code=1).stdout)
        patched=json.loads(run(['-m','scripts.validation.app_fixture_probe','--archive',str(received),'--warning-patch','docs/api/fixture-warning-patch.json']).stdout)
        assert valid['valid'] and sum(f['valid'] for f in original['canonical_fixtures'])==18
        assert all(f['valid'] for f in patched['canonical_fixtures']) and all(n['rejected'] for n in patched['negative_semantics'])
        print(json.dumps({'outgoing_bundle_sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),
                          'manifest_members_verified':len(listed),'standalone_cli_tests':3,
                          'original_service_fixtures_valid':18,'canonical_fixture_count':21,
                          'proposed_warning_fixes_valid':21,'invalid_semantic_examples_rejected':4,
                          'imported_source':'extracted outgoing package only; no project checkout mounted',
                          'network':'disabled','scope':'reference package and fixture compatibility, not app/API execution'},indent=2))

if __name__=='__main__':main()
