import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricing import PricingRecorder
from pricing_audit import audit
from pricing_report import legacy_log_summary, report


class PricingReportTest(unittest.TestCase):
    def test_denominators_keep_unknown_and_unexposed_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = PricingRecorder(Path(directory) / "pricing.sqlite")
            rec.start("counted", "lamp", 100)
            rec.opening_sent("counted", 112)
            rec.receipt("counted", 105)
            rec.complete("counted")
            rec.market_result("counted", True, "ledger seq 1")
            rec.start("unknown", "lamp", 100)
            rec.opening_sent("unknown", 112)
            rec.start("infra", "lamp", 100)
            rec.close("infra", "INFRA_ERROR", "model failure")
            totals = report(rec)["overall"]
            self.assertEqual((totals["recorded_attempts"], totals["exposed_attempts"],
                              totals["market_counted"], totals["negotiation_unresolved"]),
                             (3, 2, 1, 1))
            self.assertEqual(totals["counted_rate_per_recorded_attempt"], 0.3333)
            self.assertEqual(totals["counted_rate_per_exposed_attempt"], 0.5)
            self.assertEqual(totals["counted_margin_per_recorded_attempt"], 1.6667)

    def test_historical_missing_outcomes_remain_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.txt"
            events = [
                {"event": "seller_open", "task_id": "a"},
                {"event": "seller_counter", "task_id": "a"},
                {"event": "seller_open", "task_id": "b"},
                {"event": "seller_deal", "task_id": "b"},
                {"event": "seller_open", "task_id": "c"},
            ]
            path.write_text("".join("00:00:00 [seller] " + json.dumps(e) + "\n" for e in events))
            summary = legacy_log_summary(path, 3)
            self.assertEqual((summary["unknown"], summary["unknown_after_counter"],
                              summary["unknown_without_counter"], summary["receipt_issued"]),
                             (2, 1, 1, 1))

    def test_ledger_timing_uses_only_the_applied_result(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = PricingRecorder(Path(directory) / "pricing.sqlite")
            rec.start("a", "lamp", 100)
            rec.opening_sent("a", 112)
            rec.receipt("a", 105)
            timestamp = time.time()
            checks = {"sig_seller_ok": True}
            rec.save_ledger_observation(1, "a", "Luna Shop", "lamp", 105, True, checks, timestamp)
            rec.save_ledger_observation(2, "a", "Luna Shop", "lamp", 105, True, checks, timestamp + 1)
            rec.market_result("a", True, "ledger seq 2")
            self.assertEqual(report(rec)["ledger_lag"]["locally_issued_receipts_with_ledger_timing"], 1)

    def test_only_matching_seller_deal_log_backfills_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rec = PricingRecorder(root / "pricing.sqlite")
            rec.start("a", "lamp", 100)
            rec.opening_sent("a", 112)
            rec.offer_received("a", 105)
            rec.receipt("a", 105)
            log = root / "market-kit.log"
            log.write_text("".join("00:00:00 [seller] " + json.dumps(event) + "\n" for event in [
                {"event": "seller_open", "task_id": "a", "ask": 112},
                {"event": "seller_deal", "task_id": "a", "price": 105},
            ]))
            result = audit(rec, log, 0, apply_completions=True)
            self.assertEqual(result["counts"]["completions_backfilled"], 1)
            self.assertEqual(rec.get("a")["negotiation_status"], "COMPLETED")
            self.assertEqual(audit(rec, log, 0, apply_completions=True)["counts"].get("completions_backfilled", 0), 0)


if __name__ == "__main__":
    unittest.main()
