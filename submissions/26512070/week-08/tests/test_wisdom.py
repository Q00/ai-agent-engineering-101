"""harness.wisdom / experiment / alert: a patch request, the capability checks, approval on the current strategy,
a stale request refused, A/B approval starting an experiment, one experiment per metric, alerts without a token."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from core import atomic
from harness import alert, experiment, wisdom


def strategy(version, ceiling=0.82):
    src = Path(__file__).resolve().parent.parent / "memory" / "strategy" / "active.json"
    s = atomic.read_json(src, default=None)
    if s is None:                        # a checkout without live memory: the newest committed card
        cards = sorted((src.parents[2] / "logs" / "strategy" / "strategies").glob("v*.json"),
                       key=lambda p: int(p.stem[1:]))
        s = atomic.read_json(cards[-1])
    s = dict(s, version=version, epoch=f"e{version}")
    s.setdefault("playbook", {}).setdefault("buyer", {})["ceiling_share"] = ceiling
    return s


REQ = {"title": "buyer goods ceiling 85%", "urgency": "page",
       "change": {"playbook": {"buyer": {"ceiling_share": 0.85}}}, "why": "surplus room 0.274",
       "expected": {"metric": "surplus", "direction": "up", "size": "10%"},
       "evaluation": {"method": "before_after", "period_h": 6, "success_if": {"direction": "up", "by": 0.1}},
       "risk": "less surplus per deal", "undo": "set 0.82 again"}


class WisdomTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT", "TELEGRAM_BOT_TOKEN",
                                                    "TELEGRAM_CHAT_ID")}
        os.environ["HARNESS_ROOT"] = self.tmp.name
        os.environ["HARNESS_OUT"] = str(Path(self.tmp.name) / "out")
        os.environ["TELEGRAM_BOT_TOKEN"] = ""          # empty, not absent: never the user's real bot in a test
        os.environ["TELEGRAM_CHAT_ID"] = ""
        self.active = Path(self.tmp.name) / "memory" / "strategy" / "active.json"
        atomic.write_json(self.active, strategy(10))

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_request_and_approve(self):
        r = wisdom.request(REQ, now=1000)
        self.assertEqual(r["base"], {"playbook.buyer.ceiling_share": 0.82})
        self.assertTrue(any("no approved range" in c for c in r["checks"]))
        self.assertEqual([d["id"] for d in wisdom.status()], [r["id"]])
        out = wisdom.decide(r["id"], "approve", now=2000)
        self.assertEqual(out["installed"], 11)
        s = atomic.read_json(self.active)
        self.assertEqual((s["version"], s["playbook"]["buyer"]["ceiling_share"]), (11, 0.85))
        with self.assertRaises(ValueError):
            wisdom.decide(r["id"], "approve")                      # decided already

    def test_stale_request_refused(self):
        r = wisdom.request(REQ, now=1000)
        atomic.write_json(self.active, strategy(11, ceiling=0.8))  # someone changed the same number since
        with self.assertRaises(ValueError):
            wisdom.decide(r["id"], "approve", now=2000)

    def test_bad_change_refused(self):
        with self.assertRaises(ValueError):
            wisdom.request(dict(REQ, change={"playbook": {"buyer": {"ceiling_share": "high"}}}))

    def test_ab_starts_experiment(self):
        ab = dict(REQ, evaluation={"method": "ab", "period_h": 6, "success_if": {"direction": "up", "by": 0.1}})
        r = wisdom.request(ab, now=1000)
        out = wisdom.decide(r["id"], "approve", now=2000)
        self.assertNotIn("installed", out)
        self.assertEqual(atomic.read_json(self.active)["version"], 10)          # not installed
        arms, patch = experiment.assign("buyer", "o-1", now=3000)
        self.assertEqual(list(arms), [out["experiment"]])
        self.assertEqual(patch if arms[out["experiment"]] == "B" else {"playbook": {"buyer": {"ceiling_share": 0.85}}},
                         {"playbook": {"buyer": {"ceiling_share": 0.85}}})
        r2 = wisdom.request(ab, now=1001)
        with self.assertRaises(ValueError):                                     # one experiment per metric
            wisdom.decide(r2["id"], "approve", now=2001)
        split = [experiment.arm("x", f"o{i}") for i in range(400)]
        self.assertTrue(150 < split.count("B") < 250)

    def test_alert_without_token(self):
        self.assertFalse(alert.send("시험", "now"))
        row = alert.recent(now=None)[-1]
        self.assertFalse(row["sent"])
        self.assertIn("not set", row["not_sent"])


if __name__ == "__main__":
    unittest.main()
