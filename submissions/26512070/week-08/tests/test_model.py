"""agents.model: the breaker and the fallback, with fake backends (no model is called).

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from agents import model  # noqa: E402


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("HARNESS_ROOT")
        self.root = Path(tempfile.mkdtemp(prefix="harness-test-"))
        os.environ["HARNESS_ROOT"] = str(self.root)
        self.saved = dict(model.BACKENDS)
        self.calls = []
        self.t = 1000.0

    def tearDown(self):
        model.BACKENDS.update(self.saved)
        if model._writer:
            model._writer.close()
            model._writer = None
        if self.old is None:
            os.environ.pop("HARNESS_ROOT", None)
        else:
            os.environ["HARNESS_ROOT"] = self.old
        shutil.rmtree(self.root, ignore_errors=True)

    def fake(self, claude_rate_limited):
        def claude(m, s, p, t):
            self.calls.append("claude")
            if claude_rate_limited:
                raise model.ModelError("429 rate limit", True)
            return "from claude"

        def router(m, s, p, t):
            self.calls.append("openrouter")
            return "from openrouter"
        model.BACKENDS.update(claude=claude, openrouter=router)

    def call(self, seat="seller"):
        return model.call(seat, "hi", now=lambda: self.t)

    def test_claude_answers(self):
        self.fake(False)
        self.assertEqual(self.call(), "from claude")
        self.assertEqual(self.calls, ["claude"])

    def test_rate_limit_falls_back_then_breaker_opens_and_closes(self):
        self.fake(True)
        for _ in range(model.BREAK_AFTER):
            self.assertEqual(self.call(), "from openrouter")
        self.assertGreater(model.breaker("seller")["open_until"], self.t)
        self.calls.clear()
        self.assertEqual(self.call(), "from openrouter")
        self.assertEqual(self.calls, ["openrouter"])            # the breaker skips Claude Code
        self.t += model.BREAK_SECONDS + 1
        self.fake(False)
        self.calls.clear()
        self.assertEqual(self.call(), "from claude")             # the trial call closes it
        self.assertEqual(model.breaker("seller"), {"fails": 0, "open_until": 0})
        self.assertTrue(list((self.root / "memory" / "bus" / "alerts").glob("*.json")))

    def test_other_errors_do_not_fall_back(self):
        def claude(m, s, p, t):
            raise model.ModelError("bad request")
        model.BACKENDS.update(claude=claude)
        with self.assertRaises(model.ModelError):
            self.call()

    def test_non_l1_seat_waits_instead_of_falling_back(self):
        self.fake(True)
        with self.assertRaises(model.ModelError):
            self.call("strategy")
        self.assertEqual(self.calls, ["claude"])


if __name__ == "__main__":
    unittest.main()
