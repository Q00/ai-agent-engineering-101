"""harness.ops: emergency actions are flags the roles read, alert at once, and only clear by hand."""
import os
import tempfile
import unittest
from pathlib import Path

from harness import alert, ops


class OpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT", "TELEGRAM_BOT_TOKEN",
                                                    "TELEGRAM_CHAT_ID")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"),
                          TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_emergency(self):
        self.assertFalse(ops.stopped("restock_stop"))
        self.assertTrue(ops.trigger("restock_stop", "rules changed", now=10))
        self.assertFalse(ops.trigger("restock_stop", "again", now=11))        # once
        self.assertTrue(ops.stopped("restock_stop"))
        self.assertEqual(alert.recent(now=12)[-1]["level"], "now")
        with self.assertRaises(ValueError):
            ops.trigger("lower_prices", "not pre-approved")
        self.assertEqual(ops.clear("restock_stop", now=20)["why"], "rules changed")
        self.assertFalse(ops.stopped("restock_stop"))

    def test_shop_reach_probes_after_quiet(self):
        # 11/7 12:58-13:30: the tunnel was down, the seller alive, no buyer came in for 32 min
        import time
        log = Path(self.tmp.name) / "logs" / "market-kit.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("12:00:00 [market] shop registered again as us at https://a.example\n"
                       "12:58:31 [seller] [seller] 529ce441 completed: Deal at 100 KRW.\n", encoding="utf-8")
        t = time.localtime()
        at = lambda h, m: time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, m, 0, 0, 0, -1))
        self.assertIsNone(ops.shop_reach(at(13, 5), probe=lambda u: False))           # 6 min: not yet
        lvl, text = ops.shop_reach(at(13, 20), probe=lambda u: False)
        self.assertEqual(lvl, "now")
        self.assertIn("https://a.example", text)
        self.assertEqual(ops.shop_reach(at(13, 20), probe=lambda u: True)[0], "warn")


if __name__ == "__main__":
    unittest.main()
