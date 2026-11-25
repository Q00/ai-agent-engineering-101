"""outer.watch: warn/urgent alerts reach logs/strategy/URGENT.md; a stale inner status raises one urgent alert.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import bus  # noqa: E402
from outer import watch  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


class WatchTest(Base):
    def test_alerts_and_inner_down(self):
        state = {}
        bus.alert("restock", "urgent", "bankrupt", "filed for bankruptcy")
        bus.alert("model-seller", "info", "breaker", "closed again")
        out = watch.check(state)
        self.assertEqual(out["new_alerts"], 1)
        text = watch.urgent_path().read_text(encoding="utf-8")
        self.assertIn("filed for bankruptcy", text)
        self.assertNotIn("closed again", text)
        self.assertEqual(watch.check(state)["new_alerts"], 0)            # each alert once
        bus.write_status("inner", kit_version="4")
        self.assertTrue(watch.check(state)["inner_alive"])
        later = time.time() + watch.INNER_STALE + 5
        watch.check(state, now=later)
        watch.check(state, now=later + 60)                               # alerted once, not every pass
        self.assertEqual(sum(1 for a in bus.alerts() if a["kind"] == "inner_down"), 1)


if __name__ == "__main__":
    unittest.main()
