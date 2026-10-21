"""State and ledger invariants for the record-only price experiment."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricing import PricingRecorder
from reconcile_pricing import reconcile


class PricingRecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "pricing_state.sqlite"
        self.rec = PricingRecorder(self.path)

    def test_unknown_does_not_become_a_failed_or_counted_deal(self):
        self.rec.start("a", "lamp", 100)
        self.rec.model_call("a")
        self.rec.opening_sent("a", 112)
        self.rec.offer_received("a", 105)
        self.rec.counter_sent("a", 110)
        self.assertEqual(self.rec.get("a")["terminal_status"], "UNRESOLVED")
        self.assertIsNone(self.rec.get("a")["market_counted"])
        self.assertEqual(self.rec.stats("lamp")[0]["unresolved"], 1)
        self.assertEqual(self.rec.stats("lamp")[0]["counted"], 0)
        self.assertEqual(PricingRecorder(self.path).get("a")["offers"], [105])

    def test_receipt_requires_market_evidence_and_is_idempotent(self):
        self.assertTrue(self.rec.start("a", "chair", 180))
        self.assertFalse(self.rec.start("a", "chair", 180))
        with self.assertRaises(ValueError):
            self.rec.start("a", "lamp", 100)
        self.rec.opening_sent("a", 202)
        self.rec.receipt("a", 185)
        self.rec.receipt("a", 185)
        with self.assertRaises(ValueError):
            self.rec.receipt("a", 186)
        self.assertEqual(self.rec.get("a")["terminal_status"], "RECEIPT_PENDING")
        self.assertIsNone(self.rec.get("a")["market_counted"])
        self.rec.market_result("a", True, "ledger seq 123")
        self.rec.market_result("a", True, "ledger seq 123")
        self.assertEqual(self.rec.stats()[0]["margin_counted"], 5)
        with self.assertRaises(ValueError):
            self.rec.market_result("a", False, "conflict")

    def test_reserve_and_explicit_failure(self):
        self.rec.start("a", "bookshelf", 260)
        with self.assertRaises(ValueError):
            self.rec.opening_sent("a", 259)
        self.rec.opening_sent("a", 292)
        with self.assertRaises(ValueError):
            self.rec.receipt("a", 259)
        self.rec.close("a", "REJECTED", "seller rejected")
        self.rec.close("a", "REJECTED", "replayed")
        self.assertEqual(self.rec.stats()[0]["rejected"], 1)

    def test_retry_after_model_error_returns_to_unresolved(self):
        self.rec.start("a", "lamp", 100)
        self.rec.opening_sent("a", 112)
        self.rec.close("a", "INFRA_ERROR", "model call failed")
        self.assertEqual(self.rec.get("a")["terminal_status"], "INFRA_ERROR")
        self.rec.counter_sent("a", 105)
        self.assertEqual(self.rec.get("a")["terminal_status"], "UNRESOLVED")

    def test_reconcile_matches_task_and_receipt_not_other_shops(self):
        self.rec.start("own", "lamp", 100)
        self.rec.opening_sent("own", 112)
        self.rec.receipt("own", 103)
        entries = [
            {"seq": 1, "round": 2, "kind": "result", "data": {
                "receipt": {"shop": "Other Shop", "item": "lamp", "price": 103, "task": "own"},
                "verified": True, "checks": {"sig_seller_ok": True}}},
            {"seq": 2, "round": 2, "kind": "result", "data": {
                "receipt": {"shop": "Luna Shop", "item": "lamp", "price": 103, "task": "own"},
                "verified": True, "checks": {"sig_seller_ok": True, "sig_buyer_ok": True}}},
        ]

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps(entries).encode()

        with patch("reconcile_pricing.urlopen", return_value=Response()), \
             patch("reconcile_pricing.seek_first_relevant_entry", return_value=0):
            result = reconcile(self.rec, "https://example.test", "Luna Shop")
        self.assertEqual(result, {"cursor": 2, "matched": 1, "counted": 1, "complete": True})
        self.assertEqual(self.rec.get("own")["terminal_status"], "DEAL_COUNTED")
        self.assertEqual(PricingRecorder(self.path).ledger_cursor(), 2)


if __name__ == "__main__":
    unittest.main()
