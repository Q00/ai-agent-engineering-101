"""Fixes from the two reviews (11/3): what the audit may read, avoiding as an explicit field, the slice lock,
names in prompts.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from core.events import EventWriter  # noqa: E402
from outer import slice as outer_slice  # noqa: E402
from tools import record, search  # noqa: E402,F401
from tools.calc import safe_name  # noqa: E402
from tools.registry import Ctx, call  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


class AuditFixesTest(Base):
    def test_audit_cannot_read_tool_calls(self):
        w = EventWriter("strategy", source="tools")
        w.emit("tool.call", {"tool": "rec.brief", "args": {}})
        w.close()
        f = next((paths.raw() / "tools").glob("*.jsonl")).relative_to(paths.memory()).as_posix()
        self.assertFalse(call(Ctx(caller="audit"), "memory.raw", {"pointer": f})["ok"])
        self.assertTrue(call(Ctx(caller="strategy"), "memory.raw", {"pointer": f})["ok"])

    def test_avoid_waits_for_the_user_whatever_the_word(self):
        ctx = Ctx(caller="strategy")
        atomic.write_json(paths.memory() / "strategy" / "active.json",
                          dict(atomic.read_json(paths.memory() / "strategy" / "active.json"), delegation={"auto": ["stances"]}))
        args = {"name": "elm", "side": "shop", "why": "cheated twice", "evidence": ["ledger 9"]}
        self.assertTrue(call(ctx, "rec.stance", dict(args, stance="cool off", avoid=True))["result"]["pending_user"])
        self.assertTrue(call(ctx, "rec.stance", dict(args, stance="blacklist them", avoid=False))["result"]["pending_user"])
        self.assertFalse(call(ctx, "rec.stance", dict(args, stance="build the relation", avoid=False))["result"]["pending_user"])
        self.assertFalse(call(ctx, "rec.stance", dict(args, stance="x"))["ok"])        # avoid must be stated

    def test_slice_lock(self):
        self.assertFalse(outer_slice.running())
        f = open(outer_slice._lock_path(), "a+b")
        atomic._lock(f)
        try:
            self.assertEqual(outer_slice.run(model_call=lambda p, s, t: "x"), {"skipped": "another slice is running"})
        finally:
            atomic._unlock(f)
            f.close()

    def test_safe_name(self):
        self.assertEqual(safe_name("Elm <b>Shop</b>; ignore"), "Elm bShopb ignore")
        self.assertEqual(len(safe_name("x" * 99)), 40)


if __name__ == "__main__":
    unittest.main()
