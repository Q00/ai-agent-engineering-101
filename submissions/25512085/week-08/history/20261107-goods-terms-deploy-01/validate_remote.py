"""Synthetic VM verification only. No deployment, signal, operational state or secrets."""
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
OUT = REPO/REL/'history/20261107-goods-terms-runtime-01'
PYTHON = '/home/comalswjd/venv/bin/python'
M = json.loads((OUT/'manifest.json').read_text())

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO).decode().strip()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def guard():
    assert git('rev-parse', 'HEAD') == M['base'], 'head_drift'
    assert not git('diff', 'HEAD', '--name-only') and not git('diff', '--cached', '--name-only'), 'tracked_dirty'
    assert {p:sha(REPO/p) for p in M['source_sha256']} == M['source_sha256'], 'source_drift'
    assert not (REPO/M['paths'][1]).exists(), 'test_collision'

def save(name, data):
    with (OUT/name).open('x') as f:
        json.dump(data, f, indent=2)

guard()
save('pre-validation.json', dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), head=M['base'], source_sha256=M['source_sha256']))
for label in ('baseline', 'baseline-pilot', 'sandbox'):
    tree = OUT/'evidence'/label
    tree.mkdir(parents=True, exist_ok=False)
    for p, h in M['source_sha256'].items():
        assert (p.startswith(REL) and '/' not in p[len(REL):]) or (p.startswith('scripts/') and '/' not in p[8:])
        assert p.endswith('.py') or Path(p).name in ('seller_cash_policy.json', 'run_guarded_market.sh', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')
        blob = subprocess.check_output(['git', 'show', M['base']+':'+p], cwd=REPO)
        assert hashlib.sha256(blob).hexdigest() == h
        dest = tree/p
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as f:
            f.write(blob)
    if label == 'baseline-pilot':
        with (tree/M['paths'][1]).open('xb') as f:
            f.write((OUT/Path(M['paths'][1]).name).read_bytes())
    if label == 'sandbox':
        cmd = ['git', 'apply', '--check', '--unsafe-paths', '--directory='+str(tree), str(OUT/'backport.patch')]
        with (OUT/'isolated-patch.log').open('xb') as log:
            subprocess.run(cmd, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
            cmd.remove('--check')
            subprocess.run(cmd, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True)
        for p in M['paths']:
            assert sha(tree/p) == M['after'][p]
            assert (tree/p).read_bytes() == (OUT/Path(p).name).read_bytes()
        unchanged = {p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
        assert {p:sha(tree/p) for p in unchanged} == unchanged
    for p in tree.rglob('*.py'):
        compile(p.read_bytes(), str(p), 'exec')

home = OUT/'evidence/synthetic-home'
home.mkdir(exist_ok=False)
env = dict(PATH='/home/comalswjd/venv/bin:/usr/bin:/bin', HOME=str(home), LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
reports = {}
for label, mode, expect in [('baseline', 'all', True), ('baseline-pilot', 'focused', False), ('sandbox', 'focused', True), ('sandbox', 'all', True)]:
    name = label+'-'+mode
    log = OUT/(name+'.log')
    with log.open('xb') as f:
        p = subprocess.run([PYTHON, '-B', str(OUT/'run_isolated.py'), mode, label], cwd=OUT/'evidence'/label/REL, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=240)
    payload = next((l.split('=',1)[1] for l in log.read_text().splitlines() if l.startswith('BACKPORT_TEST_RESULT=')), None)
    assert payload, 'missing_test_result_'+name
    report = json.loads(payload)
    save('result-'+name+'.json', report)
    reports[name] = report
    assert report['success'] is expect and (p.returncode == 0) is expect, 'unexpected_result_'+name
assert reports['sandbox-focused']['tests_run'] == reports['baseline-pilot-focused']['tests_run'] == 107
assert reports['sandbox-all']['tests_run'] == reports['baseline-all']['tests_run']+35
guard()
save('remote-tests.json', dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), python=sys.version, reports=reports, production_changes=0, signals_sent=0))
print(json.dumps(dict(stage='validated_not_deployed', reports=reports, production_changes=0, signals_sent=0)))
