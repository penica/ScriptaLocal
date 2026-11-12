"""Download only inference files from an immutable Hugging Face model revision."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--repository', required=True)
parser.add_argument('--revision', required=True)
parser.add_argument('--destination', type=Path, required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository)
assert re.fullmatch(r'[0-9a-f]{40}', args.revision), 'Require immutable commit SHA'
allowed = {'README.md', 'LICENSE', 'tokenizer.json', 'added_tokens.json', 'config.json', 'generation_config.json', 'merges.txt',
           'model.safetensors', 'preprocessor_config.json', 'special_tokens_map.json',
           'tokenizer_config.json', 'vocab.json'}
metadata_url = f'https://huggingface.co/api/models/{args.repository}/revision/{args.revision}?blobs=true'
metadata = json.load(urllib.request.urlopen(metadata_url, timeout=60))
assert metadata['sha'] == args.revision
assert metadata.get('cardData', {}).get('license') == 'mit', 'Review license before provisioning'
args.destination.mkdir(parents=True, exist_ok=True)
manifest = {'repository': args.repository, 'revision': args.revision,
            'license': metadata['cardData']['license'], 'files': []}
for item in metadata['siblings']:
    name = item['rfilename']
    if name not in allowed:
        continue
    target = args.destination / name
    temporary = target.with_suffix(target.suffix + '.partial')
    url = f'https://huggingface.co/{args.repository}/resolve/{args.revision}/{name}'
    expected = item.get('lfs', {}).get('sha256')
    if not target.exists():
        print(f'Downloading {name}', flush=True)
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        with temporary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected:
            assert digest == expected, f'Checksum mismatch: {name}'
        temporary.replace(target)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if expected:
        assert digest == expected, f'Existing file checksum mismatch: {name}'
    manifest['files'].append({'path': name, 'bytes': target.stat().st_size,
                              'sha256': digest, 'upstream_lfs_sha256': expected})
assert {'model.safetensors', 'config.json', 'preprocessor_config.json', 'vocab.json'} <= {x['path'] for x in manifest['files']}
(args.destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'repository':args.repository,'revision':args.revision,
                  'files':len(manifest['files']),'bytes':sum(x['bytes'] for x in manifest['files'])}))
