"""Quota regressions: fake snapshots and harmless local child processes only."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import usage_guard as guard


class UsageGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.log = self.folder / "guard.jsonl"
        self.limits = {"session": 80, "week": 70, "check_every_seconds": .05, "probe_timeout_seconds": 1}

    @staticmethod
    def snapshot(session=10, week=5):
        return {"session": {"pct": session, "resets": "12pm"}, "week": {"pct": week, "resets": "Nov 15"}}

    def test_native_usage_parser_keeps_reset_timezone_text(self):
        text = "Current session: 56% used · resets Nov 8, 11:59am (Asia/Seoul)\n" \
               "Current week (all models): 6% used · resets Nov 15, 6:59am (Asia/Seoul)\n" \
               "Current week (Fable): 7% used\n"
        snapshot = guard.parse_usage(text)
        self.assertEqual(snapshot["session"]["pct"], 56)
        self.assertEqual(snapshot["week"]["pct"], 6)
        self.assertIn("Asia/Seoul", snapshot["week"]["resets"])

    def test_missing_usage_is_not_treated_as_zero(self):
        with self.assertRaises(ValueError):
            guard.parse_usage("usage temporarily unavailable")

    def test_both_limits_block_before_child_starts(self):
        for session, week in ((80, 5), (10, 70)):
            marker = self.folder / f"started-{session}-{week}"
            command = [sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()", str(marker)]
            code, stdout, _ = guard.run_guard(command, self.limits, self.log,
                                               probe=lambda: self.snapshot(session, week))
            self.assertEqual(code, 4)
            self.assertTrue(json.loads(stdout)["paused"])
            self.assertFalse(marker.exists())

    def test_probe_failure_blocks_launch(self):
        def fail():
            raise TimeoutError("test timeout")
        code, stdout, _ = guard.run_guard(["nonexistent-must-not-be-started"], self.limits, self.log, probe=fail)
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(stdout)["quota"], {})

    def test_crossing_limit_stops_inflight_child_without_claiming_zero_cost(self):
        snapshots = iter((self.snapshot(), self.snapshot(80)))
        code, stdout, _ = guard.run_guard([sys.executable, "-c", "import time; time.sleep(10)"],
                                           self.limits, self.log, probe=lambda: next(snapshots))
        self.assertEqual(code, 4)
        result = json.loads(stdout)
        self.assertTrue(result["paused"])
        self.assertIsNone(result["total_cost_usd"])
        self.assertTrue(list(self.folder.glob("overseer-interrupted-*.json")))

    def test_usage_becomes_unreadable_during_child_execution(self):
        n = [0]
        def read():
            n[0] += 1
            if n[0] > 1:
                raise ValueError("malformed usage")
            return self.snapshot()
        code, _, _ = guard.run_guard([sys.executable, "-c", "import time; time.sleep(10)"],
                                     self.limits, self.log, probe=read)
        self.assertEqual(code, 4)

    def test_completed_run_preserves_json_output(self):
        code, stdout, stderr = guard.run_guard([sys.executable, "-c", "print('{\"is_error\": false}')"],
                                                self.limits, self.log, probe=self.snapshot)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"is_error": False})
        self.assertEqual(stderr, "")

    def test_reset_allows_a_later_run(self):
        self.assertEqual(guard.run_guard([], self.limits, self.log, probe=lambda: self.snapshot(80))[0], 4)
        self.assertEqual(guard.run_guard([], self.limits, self.log, probe=lambda: self.snapshot(0, 0))[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
