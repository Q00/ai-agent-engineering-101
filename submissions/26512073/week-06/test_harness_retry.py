"""Offline checks: fake CLI outputs, never real negotiation results."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import harness


class RetryTests(unittest.TestCase):
    def exercise(self, outputs, timeout=90):
        clock = [0.0]
        def sleep(delay):
            clock[0] += delay
        with tempfile.TemporaryDirectory(dir=harness.BASE) as folder:
            with patch.object(harness, "BASE", Path(folder)), \
                 patch.object(harness, "find_codex", return_value="fake-codex"), \
                 patch.object(harness.subprocess, "run", side_effect=outputs) as run, \
                 patch.object(harness.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(harness.time, "sleep", side_effect=sleep) as wait:
                try:
                    result = harness.call_harness("SIMULATED RETRY TEST", timeout)
                except Exception as error:
                    result = error
                logs = [p.read_text(encoding="utf-8") for p in
                        (Path(folder) / "development_logs").glob("*.txt")]
                return result, run.call_count, wait.call_args_list, logs

    def test_rate_limit_then_success(self):
        result, calls, waits, logs = self.exercise([limited(), success()])
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(calls, 2)
        self.assertEqual(waits[0].args, (5,))
        self.assertEqual(len(logs), 2)
        self.assertTrue(any("RETRY: HTTP 429" in log for log in logs))

    def test_bounded_attempts(self):
        result, calls, waits, _ = self.exercise([limited()] * 4)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(calls, 4)
        self.assertEqual([call.args[0] for call in waits], [5, 10, 20])

    def test_deadline_prevents_retry(self):
        result, calls, waits, _ = self.exercise([limited()], timeout=5)
        self.assertIsInstance(result, TimeoutError)
        self.assertEqual(calls, 1)
        self.assertEqual(waits, [])

    def test_other_failure_not_retried(self):
        result, calls, waits, _ = self.exercise([
            subprocess.CompletedProcess([], 1, "", "authentication failed")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(calls, 1)
        self.assertEqual(waits, [])

    def test_invalid_json_not_retried(self):
        result, calls, waits, logs = self.exercise([
            subprocess.CompletedProcess([], 0, "invalid JSON", "")])
        self.assertIsInstance(result, ValueError)
        self.assertEqual(calls, 1)
        self.assertEqual(waits, [])
        self.assertIn("PARSE ERROR", logs[0])


def limited():
    return subprocess.CompletedProcess([], 1, "", "ERROR: last status: 429 Too Many Requests")


def success():
    return subprocess.CompletedProcess([], 0, '{"status":"ok"}', "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
