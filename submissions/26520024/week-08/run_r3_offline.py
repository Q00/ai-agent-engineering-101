"""Run isolated tests, retain genuine output, and prove default CLI live locks."""
import contextlib
from datetime import datetime, timezone
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def forbidden(*args, **kwargs):
    raise AssertionError('Live network or model invocation forbidden in offline verification')


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    log_dir = ROOT / 'logs' / 'round3-offline'
    log_dir.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT), pattern='test_r3*.py')
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
            patch.dict(os.environ, {'R3_ENABLE_LIVE': '0', 'R3_CLOCK_OFFSET_SECONDS': '0'}), \
            patch('core.invoke', forbidden), patch('urllib.request.urlopen', forbidden), \
            patch('httpx.HTTPTransport.handle_request', forbidden), \
            patch('httpx.AsyncHTTPTransport.handle_async_request', forbidden):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    guards = []
    env = dict(os.environ, R3_ENABLE_LIVE='0', PYTHONDONTWRITEBYTECODE='1')
    for args in (["r3_seller.py", "--port", "12345"],
                 ["r3_buyer.py", "--shops", "unused", "--order", "unused", "--item", "unused", "--budget", "1"],
                 ["r3_restock.py", "--state", "unused"],
                 ["r3_review.py", "--deal", "unused"], ["r3_controller.py"],
                 ["r3_market_kit.py", "join"]):
        proc = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, text=True,
                              capture_output=True, timeout=15)
        blocked = proc.returncode != 0 and ('R3_ENABLE_LIVE' in proc.stderr or 'locked' in proc.stderr)
        if args[0] == 'r3_restock.py' and proc.returncode == 0:
            blocked = json.loads(proc.stdout) == {'buy': []} and 'R3_ENABLE_LIVE' in proc.stderr
        guards.append({'command': args, 'blocked': blocked, 'returncode': proc.returncode})
        output.write('\nCLI lock ' + ' '.join(args) + '\n' + proc.stdout + proc.stderr)
    passed = result.wasSuccessful() and all(row['blocked'] for row in guards)
    report = {'timestamp_utc': stamp, 'mode': 'offline_test_doubles_not_market_performance',
              'python': sys.version.split()[0], 'interpreter': sys.executable,
              'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'cli_locks': guards, 'passed': passed,
              'packages': {name: importlib.metadata.version(name) for name in ('a2a-sdk', 'cryptography', 'httpx')},
              'limitations': ['No live model quality measurement', 'No actual market settlement or ranking',
                              'No deployment', 'Fake inputs/models are labelled unit-test fixtures']}
    (log_dir / (stamp + '.log')).write_text(output.getvalue(), encoding='utf-8')
    (log_dir / (stamp + '.json')).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    print('Raw log: ' + str(log_dir / (stamp + '.log')))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
