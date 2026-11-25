"""harness.fastpath: only sure, small, audited, daytime changes go in at once; a missed success line undoes them."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import atomic
from harness import experiment, fastpath, knowledge, wisdom
from tests.test_wisdom import REQ, strategy

DAY = time.mktime(time.strptime("2026-11-06 13:00", "%Y-%m-%d %H:%M"))


class FastPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT", "TELEGRAM_BOT_TOKEN",
                                                    "TELEGRAM_CHAT_ID")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"),
                          TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
        self.active = Path(self.tmp.name) / "memory" / "strategy" / "active.json"
        atomic.write_json(self.active, strategy(10))
        knowledge.write({"id": "shop:share_of_market:x", "about": "shop:share_of_market", "claim": "c",
                         "source": "data:ledger", "method": "m", "sample": {"n": 5}}, now=DAY - 60)

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def req(self, **kw):
        r = dict(REQ, change={"playbook": {"buyer": {"ceiling_share": 0.9}}},
                 why="see shop:share_of_market:x",
                 evaluation={"method": "before_after", "period_h": 3, "success_if": {"direction": "up", "by": 0.05}})
        r.update(kw)
        doc = wisdom.request(r, now=DAY)
        doc["audit"] = {"agrees": True}
        return doc

    def test_eligible_and_not(self):
        self.assertEqual(fastpath.eligible(self.req(), DAY), (True, []))
        self.assertIn("moves", " ".join(fastpath.eligible(
            self.req(change={"playbook": {"buyer": {"ceiling_share": 0.4}}}), DAY)[1]))
        self.assertIn("night (R9)", fastpath.eligible(self.req(), DAY - 8 * 3600)[1])
        no_ev = self.req(why="I think so")
        self.assertTrue(any("evidence" in w for w in fastpath.eligible(no_ev, DAY)[1]))
        disagree = self.req()
        disagree["audit"] = {"agrees": False}
        self.assertIn("the audit did not agree", fastpath.eligible(disagree, DAY)[1])

    def test_apply_then_undo(self):
        out = fastpath.try_apply(self.req(), DAY)
        self.assertTrue(out["auto"])
        self.assertEqual(atomic.read_json(self.active)["playbook"]["buyer"]["ceiling_share"], 0.9)
        with mock.patch.object(experiment, "result", return_value={"verdict": "no effect", "before": 1, "after": 1}):
            self.assertEqual(fastpath.settle(DAY + 4 * 3600), [(out["experiment"], "undone")])
        s = atomic.read_json(self.active)
        self.assertEqual((s["playbook"]["buyer"]["ceiling_share"], s["version"]), (0.82, 12))
        self.assertEqual(fastpath.settle(DAY + 5 * 3600), [])                   # once

    def test_superseded(self):
        a = wisdom.request(dict(REQ), now=DAY)
        b = wisdom.request(dict(REQ, title="again"), now=DAY + 1)
        self.assertEqual([d["id"] for d in wisdom.status()], [b["id"]])
        self.assertEqual(wisdom.status(a["id"])["status"], "superseded")


if __name__ == "__main__":
    unittest.main()
