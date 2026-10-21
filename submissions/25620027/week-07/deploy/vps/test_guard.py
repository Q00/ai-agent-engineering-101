"""Offline subprocess scenarios; every scenario retains its state and child logs."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('vps_guard', HERE / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
EVIDENCE = Path(os.environ.get('VPS_TEST_EVIDENCE', HERE / '.omo/evidence/guard'))


class GuardTests(unittest.TestCase):
    def test_authorized_daily_mode_keeps_cumulative_telemetry_without_old_stop(self):
        self.baseline.write_text('{"model_usage_count": 10001}')
        guard.DEADLINE = time.time() + .2
        with patch.dict(os.environ, {'AX_OPENAI_FALLBACK': '1'}):
            code, state = self.run_guard(self.commands('import time; time.sleep(30)'))
        self.assertEqual((code, state['reason'], state['model_usage_count']),
                         (0, 'deadline', 10001))
        self.assertIsNone(state['limit'])
        self.assertEqual(state['paid_daily_limit'], 5000)

    def setUp(self):
        self.directory = EVIDENCE / self._testMethodName
        self.directory.mkdir(parents=True, exist_ok=False)
        self.data = self.directory / 'data'
        self.baseline = self.directory / 'input-baseline.json'
        self.baseline.write_text(json.dumps({'model_usage_count': 9998}))
        self.deadline = guard.DEADLINE
        guard.DEADLINE = time.time() + 30

    def tearDown(self):
        guard.DEADLINE = self.deadline

    def commands(self, seller):
        return {
            'seller': [sys.executable, '-u', '-c', seller],
            'cloudflared': [sys.executable, '-u', '-c',
                           "import time; print('TUNNEL_STARTED'); time.sleep(30)"],
        }

    def run_guard(self, commands):
        code = guard.supervise(self.data, self.baseline, commands)
        state = json.loads((self.data / 'guard-state.json').read_text())
        with (self.directory / 'observations.jsonl').open('a') as output:
            output.write(json.dumps({'exit_code': code, 'state': state}) + '\n')
        return code, state

    def test_cap_and_restart_keeps_cumulative_baseline(self):
        commands = self.commands("import time; print('MODEL_USAGE model=fake input=1 output=2'); print('MODEL_USAGE model=fake input=1 output=2'); time.sleep(30)")
        code, state = self.run_guard(commands)
        self.assertEqual((code, state['reason'], state['model_usage_count']),
                         (0, 'model_usage_limit', 10000))
        log = (self.data / 'seller.log').read_bytes()
        self.assertEqual(guard.count_usage(self.data / 'seller.log'), 2)
        code, state = self.run_guard(commands)
        self.assertEqual((code, state['model_usage_count']), (0, 10000))
        self.assertEqual((self.data / 'seller.log').read_bytes(), log)

    def test_existing_persistent_usage_counts_on_restart(self):
        self.data.mkdir()
        (self.data / 'baseline.json').write_text(self.baseline.read_text())
        (self.data / 'seller.log').write_text('MODEL_USAGE model=prior input=1 output=1\n')
        code, state = self.run_guard(self.commands("import time; print('MODEL_USAGE model=next input=1 output=1'); time.sleep(30)"))
        self.assertEqual((code, state['model_usage_count']), (0, 10000))
        self.assertIn(b'model=prior', (self.data / 'seller.log').read_bytes())
        self.assertIn(b'model=next', (self.data / 'seller.log').read_bytes())

    def test_absolute_deadline_prevents_child_launch(self):
        guard.DEADLINE = self.deadline
        self.assertEqual(self.deadline, guard.dt.datetime.fromisoformat('2026-10-28T17:15:00+09:00').timestamp())
        guard.DEADLINE = time.time() - 1
        code, state = self.run_guard(self.commands("raise RuntimeError('must not start')"))
        self.assertEqual((code, state['reason']), (0, 'deadline'))
        self.assertFalse((self.data / 'seller.log').exists())

    def test_deadline_stops_silent_children(self):
        guard.DEADLINE = time.time() + 0.35
        start = time.monotonic()
        code, state = self.run_guard(self.commands('import time; time.sleep(30)'))
        self.assertEqual((code, state['reason']), (0, 'deadline'))
        self.assertLess(time.monotonic() - start, 3)

    def test_child_failure_returns_nonzero_for_bounded_restart(self):
        code, state = self.run_guard(self.commands("print('failed'); raise SystemExit(7)"))
        self.assertEqual((code, state['reason']), (1, 'child_exit'))
        self.assertIn('failed', (self.data / 'seller.log').read_text())

    def test_sigterm_stops_both_children_normally(self):
        timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            code, state = self.run_guard(self.commands("import time; print('SELLER_STARTED'); time.sleep(30)"))
        finally:
            timer.join()
        self.assertEqual((code, state['reason']), (0, 'signal'))
        self.assertIn('SELLER_STARTED', (self.data / 'seller.log').read_text())
        self.assertIn('TUNNEL_STARTED', (self.data / 'cloudflared.log').read_text())

    def test_truncated_log_cannot_reduce_persistent_count(self):
        self.data.mkdir()
        (self.data / 'baseline.json').write_text(self.baseline.read_text())
        (self.data / 'seller.log').write_text('')
        (self.data / 'guard-state.json').write_text('{"model_usage_count": 9999}')
        with self.assertRaises(ValueError):
            self.run_guard(self.commands('raise SystemExit(1)'))
        (self.directory / 'rejection.txt').write_text('PASS: log truncation rejected against persisted usage floor before child launch\n')

    def test_deleted_log_cannot_reduce_persistent_count(self):
        self.data.mkdir()
        (self.data / 'baseline.json').write_text(self.baseline.read_text())
        (self.data / 'guard-state.json').write_text('{"model_usage_count": 9999}')
        with self.assertRaises(ValueError):
            self.run_guard(self.commands('raise SystemExit(1)'))
        (self.directory / 'rejection.txt').write_text('PASS: missing log rejected against persisted usage floor before child launch\n')
        self.assertFalse((self.data / 'seller.log').exists())

    def test_staging_disables_only_tunnel(self):
        from unittest.mock import patch
        def inspect(_data, _baseline, commands, prepare):
            self.assertEqual(list(commands), ['seller'])
            self.assertIn('/app/deploy/vps/serve_public.py', commands['seller'])
            (self.directory / 'commands.json').write_text(json.dumps(commands, indent=2))
            return 0
        with patch.dict(os.environ, {'AX_TUNNEL_ENABLED': '0'}), patch.object(guard, 'supervise', inspect):
            self.assertEqual(guard.main(), 0)

    def test_replaced_baseline_fails_closed(self):
        self.data.mkdir()
        (self.data / 'baseline.json').write_text('{"model_usage_count": 9999}')
        with self.assertRaises(ValueError):
            self.run_guard(self.commands('raise SystemExit(1)'))
        (self.directory / 'rejection.txt').write_text('PASS: changed baseline rejected before child launch\n')
        self.assertFalse((self.data / 'seller.log').exists())

    def test_extended_cap_resumes_old_limit_without_counter_reset(self):
        self.baseline.write_text('{"model_usage_count": 4998}')
        self.data.mkdir()
        (self.data / 'baseline.json').write_text(self.baseline.read_text())
        (self.data / 'seller.log').write_text(
            'MODEL_USAGE model=prior input=1 output=1\n' * 2)
        (self.data / 'guard-state.json').write_text('{"model_usage_count": 5000}')
        guard.DEADLINE = time.time() + 0.4
        code, state = self.run_guard(self.commands(
            "import time; print('MODEL_USAGE model=continued input=1 output=1'); time.sleep(30)"))
        self.assertEqual((code, state['reason'], state['baseline'],
                          state['model_usage_count'], state['limit']),
                         (0, 'deadline', 4998, 5001, 10000))
        self.assertIn(b'model=continued', (self.data / 'seller.log').read_bytes())

    def test_lost_baseline_fails_closed(self):
        self.data.mkdir()
        (self.data / 'seller.log').write_text('MODEL_USAGE model=prior input=1 output=1\n')
        with self.assertRaises(ValueError):
            self.run_guard(self.commands('raise SystemExit(1)'))
        (self.directory / 'rejection.txt').write_text('PASS: lost baseline with existing logs rejected\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
