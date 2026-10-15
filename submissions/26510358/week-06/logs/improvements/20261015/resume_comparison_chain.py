"""Resume only unfinished cohorts after the interactive turn was interrupted."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from benchmark_a2a import stop

BASE = Path(__file__).resolve().parent
PYTHON = '/home/myeonggi/a2a-lab/.venv/bin/python'
KIT = str(ROOT.parents[2] / 'scripts/trade_test_week06.py')
ACTIVE = '/tmp/a2a-improvement-20261015/participants-final-active.json'
CANDIDATE = '/tmp/a2a-improvement-20261015/participants-stage-04-screen.json'


def save_new(path, data):
    with path.open('x') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def run(label, participants, mode, only, roles, timeout):
    out = BASE / label
    console = BASE / (label + '.console.txt')
    if out.exists() or console.exists():
        raise FileExistsError(label)
    command = [PYTHON, '-u', str(ROOT / 'benchmark_a2a.py'),
               '--participants', participants, '--out', str(out), '--kit', KIT,
               '--kind', 'cross', '--mode', mode, '--only', only, '--roles', roles,
               '--repeats', '1', '--api-url', 'http://127.0.0.1:46587/v1']
    print(json.dumps({'event': 'run_started', 'label': label,
                      'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'command': command}), flush=True)
    env = os.environ.copy()
    env['A2A_INFERENCE_GAP_SECONDS'] = '10'
    with console.open('x') as handle:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop(process)
            raise
    summary = json.loads((out / 'summary.json').read_text())
    if summary.get('source_unchanged') is not True:
        raise RuntimeError('Source verification failed: ' + label)
    print(json.dumps({'event': 'run_finished', 'label': label, 'exit_code': code,
                      'time': datetime.datetime.now(datetime.timezone.utc).isoformat()}), flush=True)


if __name__ == '__main__':
    save_new(BASE / 'comparison-interruption.json', {
        'event': 'interrupted_before_summary',
        'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'original_run': 'final-controlled-cross',
        'completed_result_ids': ['r01-lamp-pr296-buyer', 'r01-lamp-pr296-seller',
                                 'r01-lamp-pr295-buyer', 'r01-lamp-pr295-seller',
                                 'r01-lamp-pr292-buyer', 'r01-lamp-pr292-seller'],
        'unfinished_attempt': 'r01-lamp-pr291-buyer',
        'unfinished_attempt_outcome': 'interrupted; do not classify as a completed match',
        'all_related_processes_observed_stopped': True,
        'continuation': 'final-controlled-cross-resumed',
        'note': 'Keep interrupted raw evidence; repeat unfinished PR291 task and run remaining PRs in new output paths.'})
    run('final-controlled-cross-resumed', ACTIVE, 'controlled',
        'pr291,pr290,pr289,pr288', 'buyer,seller', 3600)
    run('final-pr289-native-cross', ACTIVE, 'native', 'pr289', 'buyer,seller', 1800)
    run('final-pr288-hosted-cross', ACTIVE, 'native', 'pr288-workers', 'seller', 600)
    run('candidate-pr296-seller-probe', CANDIDATE, 'controlled', 'pr296', 'buyer', 600)
