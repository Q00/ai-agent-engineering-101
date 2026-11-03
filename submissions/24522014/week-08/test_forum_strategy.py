import json
from pathlib import Path
import tempfile
import unittest

from forum_policy import queue_report
from market_intel import competition


class ForumStrategy(unittest.TestCase):
    def deal(self, ledger):
        return {"receipt": {"price": 80}, "settled": {"ledger": ledger, "sku": "A", "value": 100, "grade": "A"}}

    def test_actual_ledger_and_provisional_surplus_no_votes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(queue_report(tmp, self.deal(1), {"stars": 5}, now=10000), "queued")
            message = json.loads((Path(tmp)/"outbox.jsonl").read_text(encoding="utf8"))
            self.assertEqual(message["refs"], [1])
            self.assertEqual(message["as"], "buyer")
            self.assertNotIn("vote", message)
            self.assertIn("initial surplus 20", message["post"])
            self.assertIn("Later breakage", message["post"])

    def test_duplicate_cooldown_and_daily_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(4):
                self.assertEqual(queue_report(tmp, self.deal(i+1), {"stars": 5}, now=10000+3600*i), "queued")
            self.assertEqual(queue_report(tmp, self.deal(4), {"stars": 5}, now=25000), "already_queued")
            self.assertEqual(queue_report(tmp, self.deal(5), {"stars": 5}, now=26000), "rate_limited")
            self.assertEqual(len((Path(tmp)/"outbox.jsonl").read_text().splitlines()), 4)

    def test_unsettled_or_low_confidence_review_is_not_posted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(queue_report(tmp, self.deal(1), {"stars": 3}, now=10000), "insufficient_evidence")
            self.assertFalse((Path(tmp)/"outbox.jsonl").exists())

    def test_component_diagnostics_do_not_fabricate_missing_buyer_metrics(self):
        c = competition({"round": 3, "shops": [{"name": "kcwarni", "deals": 20, "profit": 100, "revenue": 200, "stars": 5}],
                         "buyers": [{"name": "kcwarni", "deals": 10}]})
        self.assertIsNone(c["diagnostic_components"]["buyers.forum"]["ahead_of_percent"])
        self.assertIsNone(c["rank"])
        self.assertTrue(any("fewer than 20" in a for a in c["alerts"]))


if __name__ == "__main__":
    unittest.main()
