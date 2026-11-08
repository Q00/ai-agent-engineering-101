"""harness.autopilot: code decides each request at once; tests end at 30-minute boundaries and are adopted, dropped,
undone or extended by their verdict."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import atomic
from harness import autopilot, experiment, wisdom
from tests.test_wisdom import REQ, strategy

AT = time.mktime(time.strptime("2026-11-08 02:07", "%Y-%m-%d %H:%M"))        # night: autopilot runs anyway


class AutopilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT", "TELEGRAM_BOT_TOKEN",
                                                    "TELEGRAM_CHAT_ID")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"),
                          TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
        self.active = Path(self.tmp.name) / "memory" / "strategy" / "active.json"
        atomic.write_json(self.active, strategy(10))
        autopilot.switch(True)

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def req(self, value=0.9, method="ab", agrees=True):
        doc = wisdom.request(dict(REQ, change={"playbook": {"buyer": {"ceiling_share": value}}},
                                  evaluation={"method": method, "period_h": 0.5,
                                              "success_if": {"direction": "up", "by": 0.05}}), now=AT)
        doc["audit"] = {"agrees": agrees}
        return doc

    def ceiling(self):
        return atomic.read_json(self.active)["playbook"]["buyer"]["ceiling_share"]

    def test_slot_end(self):
        self.assertEqual(time.strftime("%H:%M", time.localtime(autopilot.slot_end(AT))), "03:00")
        at = time.mktime(time.strptime("2026-11-08 02:00", "%Y-%m-%d %H:%M"))
        self.assertEqual(time.strftime("%H:%M", time.localtime(autopilot.slot_end(at))), "02:30")

    def test_ab_success_is_adopted(self):
        out = autopilot.take(self.req(), AT)
        self.assertTrue(out["auto"])
        self.assertEqual(self.ceiling(), 0.82)                                   # A/B: not installed yet
        with mock.patch.object(experiment, "result", return_value={"verdict": "success", "A": {"value": 1},
                                                                   "B": {"value": 2}}):
            self.assertEqual(autopilot.settle(AT + 1200), [])                  # slot not over
            self.assertEqual(autopilot.settle(AT + 3600), [(out["experiment"], "adopted")])
        self.assertEqual(self.ceiling(), 0.9)

    def test_too_few_extends_then_drops(self):
        out = autopilot.take(self.req(), AT)
        few = {"verdict": "too few trades yet", "A": {"value": None}, "B": {"value": None}}
        with mock.patch.object(experiment, "result", return_value=few):
            t = AT
            for _ in range(autopilot.MAX_EXTEND):
                t = experiment.ledger()["experiments"][out["experiment"]]["end"]
                self.assertEqual(autopilot.settle(t), [(out["experiment"], "extended")])
            t = experiment.ledger()["experiments"][out["experiment"]]["end"]
            self.assertEqual(autopilot.settle(t), [(out["experiment"], "dropped")])
        self.assertEqual(self.ceiling(), 0.82)

    def test_before_after_needs_audit_and_undoes(self):
        self.assertEqual(autopilot.take(self.req(agrees=False, method="before_after"), AT)["method"], "ab")
        for e in experiment.running(now=AT):
            experiment.stop(e["id"], "test reset", AT)
        out = autopilot.take(self.req(method="before_after"), AT + 1)
        self.assertEqual((out["method"], self.ceiling()), ("before_after", 0.9))
        with mock.patch.object(experiment, "result", return_value={"verdict": "no effect", "before": 1, "after": 1}):
            self.assertEqual(autopilot.settle(AT + 3600), [(out["experiment"], "undone")])
        self.assertEqual(self.ceiling(), 0.82)

    def test_refused(self):
        self.assertIn("moves", " ".join(autopilot.take(self.req(value=0.3), AT)["why"]))
        autopilot.take(self.req(), AT)
        self.assertIn("already runs", " ".join(autopilot.take(self.req(value=0.85), AT + 1)["why"]))


if __name__ == "__main__":
    unittest.main()
