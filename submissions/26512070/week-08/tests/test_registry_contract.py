"""tools.registry.call: the record carries the trade keys (C1), a result summary without secrets (C2), the failure
cause (C7), the tool version (C8) and a mark when the input was clipped (C9)."""
import unittest

from tools import registry
from tools.registry import Ctx, Refused, call, tool


class Capture:
    def __init__(self):
        self.events = []

    def emit(self, kind, data=None, **keys):
        self.events.append((kind, data, keys))


@tool("test.echo", "Echo for the contract test.", {"type": "object", "properties": {
    "text": {"type": "string", "maxLength": 5}, "fail": {"type": "boolean"}}})
def _echo(ctx, text="", fail=False):
    if fail:
        raise Refused("over the cash floor", "")
    return {"text": text, "api_token": "abc", "nested": {"sig_seller": "x", "n": 1}}


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.w = Capture()
        self.ctx = Ctx(caller="test", _writer=self.w, keys={"order": "o1", "counterpart": "s1", "bogus": "x"})

    def test_ok_call(self):
        out = call(self.ctx, "test.echo", {"text": "abcdefgh"})
        self.assertTrue(out["ok"])
        (k1, d1, keys1), (k2, d2, keys2) = self.w.events
        self.assertEqual((k1, k2), ("tool.call", "tool.result"))
        self.assertEqual(keys1, {"order": "o1", "counterpart": "s1"})       # unknown keys dropped
        self.assertEqual(keys2, keys1)
        self.assertTrue(d1["clipped"])
        self.assertEqual(d1["tool_version"], registry.TOOL_VERSION)
        self.assertIn('"api_token":"<hidden>"', d2["summary"])
        self.assertIn('"sig_seller":"<hidden>"', d2["summary"])
        self.assertNotIn("cause", d2)

    def test_refused_call(self):
        out = call(self.ctx, "test.echo", {"fail": True})
        self.assertFalse(out["ok"])
        d = self.w.events[-1][1]
        self.assertEqual(d["cause"], "limit")
        self.assertNotIn("clipped", self.w.events[0][1])

    def test_capability_marks(self):
        from harness import capability
        ctx = Ctx(caller="buyer", _writer=self.w)
        call(ctx, "test.echo", {"text": "a"})                        # not in the buyer's list: marked, still runs
        self.assertIn("not in buyer's capability list", self.w.events[-2][1]["not_permitted"])
        self.assertTrue(self.w.events[-1][1]["ok"])
        old, capability.ENFORCE = capability.ENFORCE, True
        try:
            out = call(ctx, "test.echo", {"text": "a"})
        finally:
            capability.ENFORCE = old
        self.assertFalse(out["ok"])
        self.assertEqual(self.w.events[-1][1]["cause"], "limit")
        self.assertEqual(capability.permitted("buyer", "calc.extract"), (True, None))
        self.assertFalse(capability.permitted("outer", "act.buy")[0])

    def test_long_summary_cut(self):
        s = registry.summary({"x": "y" * 1000})
        self.assertEqual(len(s), registry.SUMMARY_MAX)
        self.assertTrue(s.endswith("…"))


if __name__ == "__main__":
    unittest.main()
