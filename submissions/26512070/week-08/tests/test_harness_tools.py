"""tools/harness_tools: the new layers answer through the tool contract, and meta.propose refuses what code must
refuse (more than one number, a number the code does not read, a metric that is not fixed)."""
import os
import tempfile
import unittest
from pathlib import Path

from core import atomic
from tests.test_wisdom import REQ, strategy
from tools import harness_tools  # noqa: F401  (registers)
from tools.registry import TOOLS, Ctx, call


class HarnessToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"))
        atomic.write_json(Path(self.tmp.name) / "memory" / "strategy" / "active.json", strategy(10))
        self.ctx = Ctx(caller="meta")

    def tearDown(self):
        if self.ctx._writer is not None:
            self.ctx._writer.close()
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_lookups_answer(self):
        for name, args in [("capability.list", {"role": "buyer"}), ("wisdom.get", {}), ("approval.status", {}),
                           ("experiment.result", {}), ("knowledge.get", {}), ("participant.get", {"name": "x"}),
                           ("trajectory.search", {"role": "buyer"}), ("model.usage", {}),
                           ("summary.hourly", {"hours": 2})]:
            out = call(self.ctx, name, args)
            self.assertTrue(out["ok"], (name, out))
        self.assertIn("ceiling_share", call(self.ctx, "capability.list", {"role": "buyer"})["result"]["numbers"])
        self.assertFalse(call(self.ctx, "trajectory.get", {"id": "o-none"})["ok"])

    def test_propose_checks(self):
        ok = call(self.ctx, "meta.propose", {"report": "r", "requests": [REQ]})
        self.assertTrue(ok["ok"], ok)
        two = dict(REQ, change={"playbook": {"buyer": {"ceiling_share": 0.85, "max_shops_goods": 12}}})
        self.assertIn("one playbook number", call(self.ctx, "meta.propose", {"report": "r", "requests": [two]})["reason"])
        unread = dict(REQ, change={"playbook": {"buyer": {"nonexistent": 1}}})
        self.assertFalse(call(self.ctx, "meta.propose", {"report": "r", "requests": [unread]})["ok"])
        metric = dict(REQ, expected={"metric": "happiness", "direction": "up", "size": "1"})
        self.assertIn("not a fixed metric", call(self.ctx, "meta.propose", {"report": "r", "requests": [metric]})["reason"])
        self.assertFalse(call(self.ctx, "meta.propose", {"report": "r", "requests": [REQ, REQ, REQ]})["ok"])

    def test_meta_may_not_trade(self):
        self.assertTrue(all(n in TOOLS for n in ("trajectory.get", "score.class", "meta.propose")))
        from harness import capability
        self.assertFalse(capability.permitted("meta", "act.buy")[0])


if __name__ == "__main__":
    unittest.main()
