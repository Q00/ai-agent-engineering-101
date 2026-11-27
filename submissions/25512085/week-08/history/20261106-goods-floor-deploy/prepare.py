"""Build deployment input from committed blobs, never the dirty working tree."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
REL = 'submissions/25512085/week-08/'
BASE = '822ca8fb48c02841b16604039b50cfacfdddd666'
TARGET = 'b2150117dd67ba7be622d56b3c01c8237340913a'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO)

def blob_hash(ref, path):
    return hashlib.sha256(git('show', ref + ':' + path)).hexdigest()

assert git('rev-parse', 'HEAD').decode().strip() == TARGET
assert git('rev-parse', TARGET + '^^').decode().strip() == BASE
assert git('rev-list', '--count', BASE + '..' + TARGET).decode().strip() == '2'
paths = sorted(git('diff', '--name-only', '-z', BASE, TARGET).decode().rstrip('\0').split('\0'))
assert all(p.startswith(REL) for p in paths)
assert [p for p in paths if not any(x in p for x in ('/history/', '/logs/', '/test_'))] == [REL + 'seller.py']
bundle = OUT / 'goods.bundle'
assert not bundle.exists()
subprocess.run(['git', 'bundle', 'create', str(bundle), 'HEAD', '^' + BASE], cwd=REPO, check=True)
manifest = dict(base=BASE, target=TARGET, paths=paths,
                base_runtime={REL + 'seller.py': blob_hash(BASE, REL + 'seller.py')},
                target_runtime={REL + 'seller.py': blob_hash(TARGET, REL + 'seller.py')},
                bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
                target_files={p: blob_hash(TARGET, p) for p in paths})
with (OUT / 'manifest.json').open('x') as f:
    json.dump(manifest, f, indent=2)
    f.write('\n')
compile((OUT / 'deploy.py').read_text(), 'deploy.py', 'exec')
with tarfile.open(OUT / 'deploy-input.tar', 'x') as tf:
    for name in ('deploy.py', 'manifest.json', 'goods.bundle'):
        tf.add(OUT / name, arcname=name)
print(json.dumps(dict(base=BASE, target=TARGET, paths=len(paths), bundle_sha256=manifest['bundle_sha256'])))
