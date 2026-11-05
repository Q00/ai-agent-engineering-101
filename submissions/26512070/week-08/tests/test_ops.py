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


if __name__ == "__main__":
    unittest.main()
