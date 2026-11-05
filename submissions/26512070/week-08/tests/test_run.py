"""harness.run.once: every job runs when due, a failing job is recorded and the rest still run, a rules change
reaches the health pass, the meta cycle starts once per even hour."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import run


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"))

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_once(self):
        now = 1793905200.0          # 11/6 04:00 KST: an even hour
        health = mock.Mock(return_value={"findings": []})
        with mock.patch.object(run, "collect_round", side_effect=RuntimeError("market down")), \
                mock.patch.object(run, "run_forum", return_value="ok"), \
                mock.patch.object(run, "start_meta", return_value={"started": 1}) as meta, \
                mock.patch("harness.rules_watch.check", return_value={"rules": {"changed": True}}), \
                mock.patch("harness.ops.health", health), \
                mock.patch("outer.benchmark.build", return_value={"ts": now}), \
                mock.patch("harness.score_class.latest", return_value={}), \
                mock.patch("harness.participant.build", return_value=({}, (0, 1))), \
                mock.patch("harness.metrics.report", side_effect=lambda r, n: {"role": r}), \
                mock.patch("harness.capability.write", return_value="cap.json"):
            state = {}
            done = run.once(state, now)
            self.assertTrue(done["collect"].startswith("failed: RuntimeError"))
            self.assertEqual(done["forum"], "ok")
            health.assert_called_once_with(now, rules_changed=True)
            self.assertEqual(done["meta"], {"started": 1})
            done2 = run.once(state, now + 60)          # nothing due a minute later
            self.assertEqual(done2, {})
            self.assertEqual(meta.call_count, 1)

    def test_meta_due(self):
        self.assertIsNotNone(run.meta_due(1793905200.0, None))            # 04:00
        self.assertIsNone(run.meta_due(1793905200.0 + 3600, None))         # 05:00
        sid = run.meta_due(1793905200.0, None)
        self.assertIsNone(run.meta_due(1793905200.0 + 600, sid))           # same hour, once


if __name__ == "__main__":
    unittest.main()
