"""Extract only new allowlisted evidence; never overwrite a local artifact."""
import hashlib
import json
from pathlib import Path
import tarfile

out = Path(__file__).resolve().parent
preserve = {'deploy.py', 'verify-final.py', 'manifest.json'}
allowed = preserve | {'remote-before.json', 'remote-applied.json', 'remote-tests.json',
                      'signal.json', 'remote-after.json', 'final-check.json',
                      'backup-sha256.json', 'backup/seller.py', 'cherry-pick-console.log',
                      'focused-offline.log', 'all-offline.log'}
with tarfile.open(out / 'evidence-export.tar') as tf:
    assert {m.name for m in tf} == allowed
    for member in tf:
        assert member.isfile() and member.name in allowed
        source = tf.extractfile(member).read()
        path = out / member.name
        if member.name in preserve:
            # SCP may preserve CRLF differently across operating systems; source
            # helpers and the JSON manifest must nevertheless match exactly.
            assert path.read_bytes() == source, 'local_input_differs_' + member.name
            continue
        assert not path.exists(), 'existing_evidence_would_be_overwritten'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(source)
before = json.loads((out / 'remote-before.json').read_text())
final = json.loads((out / 'final-check.json').read_text())
manifest = json.loads((out / 'manifest.json').read_text())
assert hashlib.sha256((out / 'backup/seller.py').read_bytes()).hexdigest() == next(iter(before['runtime_sha256'].values()))
assert final['runtime_sha256'] == manifest['target_runtime']
assert final['protected_evidence_sha256'] == before['protected_evidence_sha256']
assert final['tests']['focused']['tests_run'] == 72 and final['tests']['all']['tests_run'] == 384
assert all(r['success'] and not r['failures'] and not r['errors'] for r in final['tests'].values())
for name in ('deploy.py', 'prepare.py', 'verify-final.py'):
    compile((out / name).read_text(), name, 'exec')
print(json.dumps(dict(local_evidence_verified=True, remote_head=final['head'],
                     runtime_sha256=final['runtime_sha256'], tests=final['tests'])))
