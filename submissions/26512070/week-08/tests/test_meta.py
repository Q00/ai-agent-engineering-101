"""harness.meta.cycle with stand-in models: requests reach the approval path with the audit, a disagreement becomes a
dispute, night requests wait for the page, a failed seat does not stop the cycle."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import atomic
from harness import meta, wisdom
from tests.test_wisdom import REQ, strategy


class MetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT", "TELEGRAM_BOT_TOKEN",
                                                    "TELEGRAM_CHAT_ID")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"),
                          TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
        atomic.write_json(Path(self.tmp.name) / "memory" / "strategy" / "active.json", strategy(10))

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def run_cycle(self, hour, audit_agrees, analyst_fails=False):
        req = dict(REQ, urgency="now")

        def call(seat, prompt):
            if seat == "analyst":
                if analyst_fails:
                    raise RuntimeError("timeout")
                return "buyer surplus fell"
            if seat == "strategy":
                return "```json\n" + json.dumps({"report": "r", "requests": [req]}) + "\n```"
            return json.dumps({"reviews": [{"title": req["title"], "agrees": audit_agrees, "points": ["p"],
                                            "counter": "fewer deals close"}]})
        now = 1793890000 + (hour - 1) * 3600          # 11/6 01:xx KST + hours
        with mock.patch.object(meta, "assemble", return_value=("ctx", {})):
            return meta.cycle(now=now, call=call)

    def test_day_dispute(self):
        rec = self.run_cycle(hour=13, audit_agrees=False)
        self.assertEqual(rec["steps"]["proposal"], {"ok": True, "requests": 1})
        doc = wisdom.status(rec["requests"][0]["id"])
        self.assertEqual(doc["urgency"], "now")
        self.assertEqual(doc["dispute"]["audit"], "fewer deals close")

    def test_night_and_failed_seat(self):
        rec = self.run_cycle(hour=3, audit_agrees=True, analyst_fails=True)
        self.assertFalse(rec["steps"]["analysis"]["ok"])
        doc = wisdom.status(rec["requests"][0]["id"])
        self.assertEqual(doc["urgency"], "page")
        self.assertNotIn("dispute", doc)


if __name__ == "__main__":
    unittest.main()
