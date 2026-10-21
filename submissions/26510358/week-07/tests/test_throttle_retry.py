"""TEST ONLY: provider backoff honors deadlines, without extra per-caller sleeps."""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness
from inference_pool import cooldown_remaining, defer_pool


class ThrottleRetryTests(unittest.TestCase):
    def test_explicit_provider_delay_and_short_exponential_default(self):
        self.assertEqual([harness.throttle_delay('429', n) for n in range(1, 5)], [5, 10, 20, 40])
        self.assertEqual(harness.throttle_delay('429 Retry-After: 120', 1), 120)
        self.assertEqual(harness.throttle_delay('Too Many Requests; retry in 0.5 seconds', 2), 0.5)

    def test_long_provider_delay_is_shared_and_not_truncated_to_sixty_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            defer_pool(120, directory=directory)
            defer_pool(1, directory=directory)
            self.assertGreater(cooldown_remaining(Path(directory)), 119)
            # Older live generations can still read their original float format.
            self.assertIsInstance(float((Path(directory) / 'cooldown').read_text()), float)

    def test_reboot_stale_legacy_value_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'cooldown').write_text(str(time.monotonic() + 100000))
            self.assertEqual(cooldown_remaining(Path(directory)), 0)

    def run_fake_cli(self, deadline=None):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            trace = SimpleNamespace(write=Mock(), directory=Path(directory), role='test-only', run_id='test')
            def process(cmd, **kwargs):
                calls.append(cmd)
                output = Path(cmd[cmd.index('--output-last-message') + 1])
                output.write_text(json.dumps({'action': 'reject', 'price': None, 'offer': None,
                                             'item_id': 'lamp', 'reason': 'TEST ONLY'}))
                result = Mock(returncode=1 if len(calls) == 1 else 0)
                result.communicate.return_value = ('', '429 Too Many Requests' if len(calls) == 1 else '')
                return result
            with patch.object(harness, 'invocation_slot', return_value=nullcontext()), \
                    patch.object(harness.subprocess, 'Popen', side_effect=process), \
                    patch.object(harness, 'defer_pool') as defer, \
                    patch.object(harness.time, 'sleep') as sleep:
                if deadline:
                    with self.assertRaises(harness.HarnessThrottleError):
                        harness.Harness('seller', trace).decide({}, attempts=2, deadline=deadline)
                else:
                    self.assertEqual(harness.Harness('seller', trace).decide({}, attempts=2)['action'], 'reject')
                defer.assert_called_once_with(5)
                sleep.assert_not_called()
        return calls

    def test_rate_limit_then_success_waits_through_admission_only(self):
        self.assertEqual(len(self.run_fake_cli()), 2)

    def test_retry_after_deadline_does_not_issue_another_request(self):
        self.assertEqual(len(self.run_fake_cli(time.monotonic() + 4)), 1)


class ProviderRecoveryTests(unittest.TestCase):
    def test_recovery_serializes_roles_and_releases_after_completion(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from inference_pool import pool_slot
        from inference_queue import QueueTimeout
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A 429 with zero retry delay still activates the recovery gate.
            defer_pool(0, directory=root)
            def seller_probe():
                with pool_slot('seller', directory=root):
                    entered.set()
                    release.wait(2)
            with ThreadPoolExecutor(max_workers=1) as workers:
                future = workers.submit(seller_probe)
                self.assertTrue(entered.wait(1))
                try:
                    # Buyer has its own role lane; only recovery serializes it.
                    with self.assertRaises(QueueTimeout):
                        with pool_slot('buyer', directory=root, deadline=time.monotonic()+0.08):
                            self.fail('Provider recovery must not start parallel requests')
                finally:
                    release.set()
                future.result(timeout=2)
            # Explicitly mark the test recovery window expired (no production override).
            (root/'throttled-at').write_text(str(time.time()-61))
            with pool_slot('buyer', directory=root, deadline=time.monotonic()+0.1):
                pass


if __name__ == '__main__':
    unittest.main()
