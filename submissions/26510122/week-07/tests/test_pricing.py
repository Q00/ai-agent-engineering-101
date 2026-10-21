"""State and ledger invariants for the record-only price experiment."""

import json
import sqlite3
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
        self.assertEqual(self.rec.get("a")["negotiation_status"], "OPEN")

    def test_message_id_deduplicates_observed_offers_and_counters(self):
        self.rec.start("a", "lamp", 100)
        self.rec.opening_sent("a", 112)
        self.rec.offer_received("a", 105, "message-1")
        self.rec.offer_received("a", 105, "message-1")
        self.rec.counter_sent("a", 110, "message-1")
        self.rec.counter_sent("a", 110, "message-1")
        self.assertEqual(self.rec.get("a")["offers"], [105])
        self.assertEqual([e["kind"] for e in self.rec.events_for("a")].count("counter_sent"), 1)
        with self.assertRaises(ValueError):
            self.rec.offer_received("a", 106, "message-1")

    def test_negotiation_and_ledger_status_are_independent(self):
        self.rec.start("a", "chair", 180)
        self.rec.opening_sent("a", 202)
        self.rec.receipt("a", 185)
        self.rec.close("a", "CANCELLED", "A2A cancellation after receipt")
        last_activity = self.rec.get("a")["last_activity_at"]
        self.rec.market_result("a", True, "ledger seq 124")
        row = self.rec.get("a")
        self.assertEqual((row["negotiation_status"], row["ledger_status"]), ("CANCELLED", "COUNTED"))
        self.assertEqual(row["last_activity_at"], last_activity)

    def test_late_ledger_observation_recovers_receipt(self):
        self.rec.save_ledger_observation(20, "a", "Luna Shop", "lamp", 105, True,
                                         {"sig_seller_ok": True, "sig_buyer_ok": True}, 1700000000.0)
        self.rec.start("a", "lamp", 100)
        self.rec.opening_sent("a", 112)
        self.assertEqual(self.rec.apply_ledger_observations("Luna Shop"),
                         {"matched": 1, "counted": 1, "receipt_recovered": 1})
        row = self.rec.get("a")
        self.assertEqual((row["receipt_issued"], row["final_price"], row["ledger_status"]),
                         (1, 105, "COUNTED"))
        self.assertEqual(row["negotiation_status"], "OPEN")
        self.assertEqual(self.rec.apply_ledger_observations("Luna Shop")["matched"], 0)

    def test_legacy_database_migrates_without_discarding_rows(self):
        legacy_path = Path(self.tmp.name) / "legacy.sqlite"
        with sqlite3.connect(legacy_path) as db:
            db.executescript("""
                CREATE TABLE trades(task_id TEXT PRIMARY KEY,item TEXT NOT NULL,reserve INTEGER NOT NULL,
                    assigned_arm TEXT NOT NULL,opening_ask_actual INTEGER,offers TEXT NOT NULL,
                    opened_at TEXT NOT NULL,last_activity_at TEXT NOT NULL,terminal_status TEXT NOT NULL,
                    receipt_issued INTEGER NOT NULL,market_counted INTEGER,final_price INTEGER,
                    model_calls INTEGER NOT NULL,status_evidence TEXT);
                CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,
                    at TEXT NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL);
                INSERT INTO trades VALUES('old','lamp',100,'legacy_cap_1.12',113,'[100]',
                    '2026-10-21T00:00:00+00:00','2026-10-21T00:01:00+00:00',
                    'DEAL_COUNTED',1,1,100,2,'ledger seq 10');
                INSERT INTO events(task_id,at,kind,data) VALUES
                    ('old','2026-10-21T00:00:10+00:00','receipt_issued','{"price":100}');
            """)
        migrated = PricingRecorder(legacy_path)
        row = migrated.get("old")
        self.assertEqual(row["ledger_status"], "COUNTED")
        self.assertEqual(row["negotiation_status"], "RECEIPT_ISSUED")
        self.assertEqual(row["last_activity_at"], "2026-10-21T00:00:10+00:00")
        self.assertEqual(len(PricingRecorder(legacy_path).all_trades()), 1)

    def test_recorder_error_is_contained_at_seller_boundary(self):
        from seller import Seller

        class BrokenRecorder:
            def start(self, *args):
                raise sqlite3.OperationalError("test-only disk failure")

        seller = Seller("Luna Shop", {"lamp": 100}, recorder=BrokenRecorder())
        with patch("seller.trace") as trace:
            seller.observe("start", "task", "lamp", 100)
        trace.assert_called_once()
        self.assertEqual(trace.call_args.args[0], "pricing_record_error")

    def test_reconcile_matches_task_and_receipt_not_other_shops(self):
        self.rec.start("own", "lamp", 100)
        self.rec.opening_sent("own", 112)
        self.rec.receipt("own", 103)
        entries = [
            {"seq": 1, "ts": 1700000000.0, "round": 2, "kind": "result", "data": {
                "receipt": {"shop": "Other Shop", "item": "lamp", "price": 103, "task": "own"},
                "verified": True, "checks": {"sig_seller_ok": True}}},
            {"seq": 2, "ts": 1700000001.0, "round": 2, "kind": "result", "data": {
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
        self.assertEqual(result, {"cursor": 2, "matched": 1, "counted": 1,
                                  "receipt_recovered": 0, "complete": True})
        self.assertEqual(self.rec.get("own")["terminal_status"], "DEAL_COUNTED")
        self.assertEqual(PricingRecorder(self.path).ledger_cursor(), 2)


if __name__ == "__main__":
    unittest.main()
