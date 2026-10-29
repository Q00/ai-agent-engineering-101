"""Synthetic observation windows; counts never come from live purchases."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

WEEK08 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEEK08 / "ops"))
from snapshot_evidence import capture, summarize_log, timestamp


def event(stage, price=None, time="2026-10-29T01:00:00+00:00", task="t1"):
    data = {"event": "trade_stage", "side": "seller", "task_id": task,
            "stage": stage, "item": "lamp", "time": time}
    if price is not None:
        data["price"] = price
    return "10:00:00 [seller] " + json.dumps(data) + "\n"


class SnapshotEvidence(unittest.TestCase):
    def setUp(self):
        private = WEEK08 / ".market"
        private.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=private)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def summary(self, text, since=None):
        path = self.root / "kit.log"
        path.write_text(text)
        return summarize_log(path, self.root / "memory.json", since)

    def test_opening_is_one_per_started_task_and_counter_is_not_an_opening(self):
        text = event("quote_issued", 999, task="unknown")
        text += event("negotiation_started") + event("quote_issued", 110) + event("quote_issued", 100)
        self.assertEqual(self.summary(text)["opening_quotes"]["lamp"], {"n": 1, "mean": 110, "min": 110, "max": 110})

    def test_local_receipts_and_quoted_peer_settlements_do_not_count(self):
        text = event("receipt_issued", 100)
        text += '10:00:00 [buyer] {"remote_message":"[acp] settled lamp at 100, ledger #5"}\n'
        text += '10:00:00 [acp] settled lamp at 100, ledger #7\n' * 2
        summary = self.summary(text)
        self.assertEqual(summary["seller_round3_settlement_lines"], 1)
        self.assertEqual(summary["buyer_round3_settlements"], 0)

    def test_since_excludes_undated_and_old_quotes(self):
        text = event("negotiation_started") + event("quote_issued", 100, time=None)
        text += event("negotiation_started", task="t2") + event("quote_issued", 101, task="t2")
        self.assertEqual(self.summary(text, timestamp("2026-11-02T00:00:00+09:00"))["opening_quotes"], {})
        with self.assertRaises(ValueError):
            timestamp("2026-11-02T00:00:00")

    def test_output_outside_student_log_directory_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            capture(self.root / "not-logs", self.root / "none", "https://example.invalid", "fixture")

    def test_boolean_and_infinite_quote_prices_are_ignored(self):
        text = event("negotiation_started") + event("quote_issued", True)
        text += event("negotiation_started", task="t2") + event("quote_issued", float("inf"), task="t2")
        self.assertEqual(self.summary(text)["opening_quotes"], {})


if __name__ == "__main__":
    unittest.main()
