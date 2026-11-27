"""Read-only preconditions, sanitized process argv metadata; no environment/state reads."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
STAGE = REPO / REL / 'history/20261107-goods-c-only-runtime-06'
manifest = json.loads((STAGE / 'manifest.json').read_text())
def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO).decode().strip()
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(pid):
    p = Path('/proc') / str(pid)
    f = (p / 'stat').read_text().rsplit(')', 1)[1].split()
    args = (p / 'cmdline').read_bytes().decode().split('\0')[:-1]
    # Never expose arbitrary command arguments (kit may contain credentials).
    scripts = [a for a in args if re.fullmatch(r'[\w/.-]+\.py', a)]
    data = dict(pid=pid, ppid=int(f[1]), starttime=f[19], cwd=str((p/'cwd').resolve()), scripts=scripts)
    if pid == 299169:
        assert len(args) == 4 and args[1] == 'seller.py' and args[2] == '--port' and args[3].isdigit(), 'unexpected_seller_argv'
        assert re.fullmatch(r'[\w/.-]*python[\d.]*', args[0]), 'unexpected_interpreter'
        data['seller_argv'] = args
        data['port'] = int(args[3])
    return data
report = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), head=git('rev-parse','HEAD'),
              dirty=git('diff','HEAD','--name-only'), staged=git('diff','--cached','--name-only'),
              source_sha256={p:sha(REPO/p) for p in manifest['source_sha256']},
              candidate_sha256={p:sha(STAGE/'evidence/sandbox'/p) for p in manifest['paths']},
              processes=[meta(p) for p in (43679,43696,299169)])
assert report['head'] == manifest['base'] and not report['dirty'] and not report['staged'], 'head_or_dirty_drift'
assert report['source_sha256'] == manifest['source_sha256'], 'source_drift'
assert report['candidate_sha256'] == manifest['after'], 'candidate_drift'
assert not (REPO/manifest['paths'][1]).exists(), 'fixture_collision'
for process, start in zip(report['processes'], ['1037901','1038186','7735542']):
    assert process['starttime'] == start, 'process_drift'
parent = meta(report['processes'][2]['ppid'])
report['seller_parent'] = parent
print(json.dumps(report))
