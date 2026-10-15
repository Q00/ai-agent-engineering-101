"""Finite, sequential follow-up: test a promising candidate; otherwise validate active."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
PYTHON = '/home/myeonggi/a2a-lab/.venv/bin/python'
ACTIVE_SHA = '07829f68cf786dc71eee06fafb7cf988722e22b6b85e4b4e7453e0c1561508fb'
sys.path.insert(0, str(ROOT))
from benchmark_a2a import stop


def write_new(path, data):
    with path.open('x') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def execute(command, console, timeout, gap):
    print(json.dumps({'event': 'command_started', 'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'command': command}), flush=True)
    env = {**os.environ, 'A2A_INFERENCE_GAP_SECONDS': str(gap)}
    with console.open('x') as handle:
        child = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                                 cwd=ROOT, env=env, start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop(child)
            raise
    print(json.dumps({'event': 'command_finished', 'returncode': code}), flush=True)
    return code


if __name__ == '__main__':
    print(json.dumps({'event': 'waiting_for_probe_and_chain_completion', 'pid': os.getpid()}), flush=True)
    deadline = time.monotonic() + 5400
    while True:
        console = BASE / 'resumed-comparison-chain.console.txt'
        if console.is_file():
            events = [json.loads(line) for line in console.read_text().splitlines() if line.startswith('{')]
            if any(row.get('event') == 'run_finished' and row.get('label') == 'candidate-pr296-seller-probe'
                   for row in events):
                break
        if time.monotonic() >= deadline:
            raise TimeoutError('Comparison chain did not complete; no validation launched')
        time.sleep(2)
    probe = json.loads((BASE / 'candidate-pr296-seller-probe/summary.json').read_text())
    if probe.get('source_unchanged') is not True or probe.get('attempts') != 1:
        raise ValueError('Probe provenance/attempt count invalid')
    decision = {'probe': 'candidate-pr296-seller-probe/summary.json',
                'candidate_probe_valid': probe['completed_valid'] == 1,
                'automatic_promotion': False,
                'note': 'Single PR296 case is exploratory and a development diagnostic, not independent proof of superiority.'}
    if decision['candidate_probe_valid']:
        out = BASE / 'candidate-env2-native-confirm'
        if out.exists():
            raise FileExistsError(out)
        execute([PYTHON, '-u', str(ROOT / 'benchmark_a2a.py'),
                 '--participants', '/tmp/a2a-improvement-20261015/participants-stage-04-screen.json',
                 '--out', str(out), '--kit', str(ROOT.parents[2] / 'scripts/trade_test_week06.py'),
                 '--mode', 'native', '--kind', 'fixed', '--only', 'ours',
                 '--roles', 'buyer,seller', '--repeats', '3'],
                BASE / 'candidate-env2-native-confirm.console.txt', 2400, 10)
        summary = json.loads((out / 'summary.json').read_text())
        valid = summary.get('source_unchanged') is True and summary.get('completed_valid') == 6
        means = {}
        for role in ('buyer', 'seller'):
            rows = [row for row in summary['results'] if row['participant_role'] == role]
            means[role] = sum(row['price'] for row in rows) / 3 if len(rows) == 3 and all(row['completed_valid'] for row in rows) else None
        passes = valid and means['buyer'] <= 101.67 and means['seller'] >= 120
        decision.update(candidate_confirmation='candidate-env2-native-confirm/summary.json',
                        means=means, predeclared_price_gate_passed=passes)
        if passes:
            decision.update(next_action='Root must evaluate candidate-specific cross/safety/official evidence before promotion.',
                            active_policy_changed=False)
            write_new(BASE / 'stage-04-opponent-probe-decision.json', decision)
            print(json.dumps({'event': 'candidate_requires_further_evaluation', 'decision': decision}), flush=True)
            raise SystemExit(0)
    decision.update(selected='existing_active', selected_sha256=ACTIVE_SHA, active_policy_changed=False,
                    reason='Candidate did not pass the exploratory opponent probe and/or predeclared real price confirmation gate.')
    write_new(BASE / 'stage-04-opponent-probe-decision.json', decision)
    execute([PYTHON, '-u', str(BASE / 'run_final_validation.py'), '--label', 'final-active',
             '--attempt', '06', '--expected-policy-sha', ACTIVE_SHA],
            BASE / 'final-validation-resumed-chain.console.txt', 3300, 5)
