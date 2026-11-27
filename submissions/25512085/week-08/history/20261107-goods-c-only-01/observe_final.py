"""Read-only final preconditions. No HTTP, logs, runtime state, env or signals."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
OUT = REPO/REL/'history/20261107-goods-c-only-runtime-02'
manifest = json.loads((OUT/'manifest.json').read_text())

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO).decode().strip()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

report = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              head=git('rev-parse', 'HEAD'), dirty=git('diff', 'HEAD', '--name-only'),
              staged=git('diff', '--cached', '--name-only'),
              source_sha256={p:sha(REPO/p) for p in manifest['source_sha256']},
              candidate_sha256={p:sha(OUT/'evidence/sandbox'/p) for p in manifest['paths']},
              original_validator_sha256=sha(REPO/REL/'history/20261107-goods-terms-runtime-01/validate_remote.py'),
              pending_sessions='unknown_not_inspected', production_replacements=0, signals_sent=0)
report['processes'] = []
for role, pid in [('kit', 43679), ('tunnel', 43696), ('seller', 299169)]:
    proc = Path('/proc')/str(pid)
    fields = (proc/'stat').read_text().rsplit(')', 1)[1].split()
    report['processes'].append(dict(role=role, pid=pid, starttime=fields[19],
                                    cwd=str((proc/'cwd').resolve())))
assert report['head'] == manifest['base'] and not report['dirty'] and not report['staged']
assert report['source_sha256'] == manifest['source_sha256'], 'production_source_drift'
assert report['candidate_sha256'] == manifest['after'], 'candidate_drift'
assert not (REPO/manifest['paths'][1]).exists(), 'test_collision'
print(json.dumps(report))
