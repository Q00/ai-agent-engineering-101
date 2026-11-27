"""Verify completed remote validation evidence without inventing a final observation."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
manifest = json.loads((OUT/'manifest.json').read_text())
result = json.loads((OUT/'remote-evidence/remote-tests.json').read_text())
assert manifest['remote'].endswith('20261107-goods-c-only-runtime-05')
assert result['production_changes'] == result['signals_sent'] == 0
assert result['reports']['sandbox-focused'] == dict(mode='focused', tree='sandbox', tests_run=119,
                                                  failures=0, errors=0, skipped=0, success=True)
assert result['reports']['sandbox-all'] == dict(mode='all', tree='sandbox', tests_run=487,
                                              failures=0, errors=0, skipped=2, success=True)
for path, digest in manifest['after'].items():
    blob = subprocess.check_output(['git', 'show', manifest['source']+':'+path], cwd=REPO)
    assert hashlib.sha256(blob).hexdigest() == digest
    assert (REPO/path).read_bytes().replace(b'\r\n', b'\n') == blob
    assert (OUT/Path(path).name).read_bytes() == blob
report = dict(source_commit=manifest['source'], candidate_sha256=manifest['after'],
              validation_completed_utc=result['utc'], focused_pass=119, full_pass=485, skipped=2,
              production_changes=0, signals_sent=0, final_process_observation='not_confirmed_timeout')
with (OUT/'verify-validated-artifacts.json').open('x', encoding='utf-8') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps(report))
