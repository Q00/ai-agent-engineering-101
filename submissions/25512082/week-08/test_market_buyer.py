from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buyer import BuyerOutcome, Quote
from market_buyer import (
    MarketBuyerError, SavedState, SettlementHistory, Shop, _candidate_offer,
    _accept_policy, _receipt_precheck, batch_spend_cap, choose_shop, current_saved_state,
    paid_total, per_order_spend_cap, rank_verified_quotes, run, saved_after,
    saved_delta, settlement_history, settlement_probability, state_dir_for,
    stockout_pairs,
)


class FakeBuyer:
    def __init__(self, url: str) -> None:
        self.url = url
        self.last_offer = 100

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def buy(self, item: str, budget: int) -> BuyerOutcome:
        receipt = {"shop": "Good", "item": item, "price": 100, "task": "task-1", "sig_seller": "signature"}
        return BuyerOutcome("deal", "task-1", 100, receipt)


class MarketBuyerTests(unittest.TestCase):
    def test_round3_ranking_prefers_saved_rate_not_absolute_surplus(self):
        cheap_high_rate = Shop("Cheap high rate", "https://cheap.test", ({"stars": 5, "review": "Good."},))
        expensive_low_rate = Shop("Expensive low rate", "https://expensive.test", ({"stars": 4, "review": "Good."},))
        suspicious = Shop("Suspicious", "https://suspicious.test", ({"stars": 1, "review": "Receipt overcharged, avoid."},))
        rows = [
            (expensive_low_rate, object(), Quote(expensive_low_rate.url, "A", 100_000, 70_000, {})),
            (suspicious, object(), Quote(suspicious.url, "B", 10_000, 5_000, {})),
            (cheap_high_rate, object(), Quote(cheap_high_rate.url, "C", 10_000, 6_000, {})),
        ]
        ranked = rank_verified_quotes(rows, saved=SavedState(0.0184, 18))
        self.assertEqual([row[0].name for row in ranked], ["Suspicious", "Cheap high rate", "Expensive low rate"])

    def test_same_discount_prefers_lower_spend(self):
        cheap = Shop("Cheap", "https://cheap.test", ())
        expensive = Shop("Expensive", "https://expensive.test", ())
        rows = [
            (expensive, object(), Quote(expensive.url, "A", 100_000, 70_000, {})),
            (cheap, object(), Quote(cheap.url, "B", 10_000, 7_000, {})),
        ]
        ranked = rank_verified_quotes(rows, saved=SavedState(0.0184, 18))
        self.assertEqual([row[0].name for row in ranked], ["Cheap", "Expensive"])

    def test_observed_settlement_history_can_outweigh_small_rate_difference(self):
        reliable = Shop("Reliable", "https://reliable.test", ())
        unknown = Shop("Unknown", "https://unknown.test", ())
        rows = [
            (unknown, object(), Quote(unknown.url, "A", 10_000, 6_000, {})),
            (reliable, object(), Quote(reliable.url, "B", 10_000, 6_500, {})),
        ]
        history = {reliable.url: SettlementHistory(settled=9, rejected=0)}
        ranked = rank_verified_quotes(rows, saved=SavedState(0.0184, 18), history=history)
        self.assertEqual([row[0].name for row in ranked], ["Reliable", "Unknown"])

    def test_patch35_saved_math(self):
        state = SavedState(0.0184, 18)
        expected = (0.0184 * 118 + 0.30) / 119
        self.assertAlmostEqual(saved_after(state, 0.30), expected)
        self.assertAlmostEqual(saved_delta(state, 0.30), expected - 0.0184)
        self.assertLess(saved_delta(state, 0.01), 0)

    def test_broken_goods_contributions_follow_official_rule(self):
        state = SavedState(0.0184, 18)
        self.assertLess(saved_delta(state, -1), 0)  # broken, no warranty
        self.assertLess(saved_delta(state, 0), 0)   # broken and fully refunded

    def test_bad_reviews_are_avoided(self):
        bad = Shop("Bad", "http://bad.test", ({"stars": 1, "review": "Receipt overcharged, avoid."},))
        good = Shop("Good", "http://good.test", ({"stars": 5, "review": "Fair price, receipt matched."},))
        self.assertEqual(choose_shop((bad, good), {"shops": {}}).shop.name, "Good")

    def test_test_and_runtime_state_are_separate(self):
        self.assertEqual(state_dir_for(Path(".market/test-shops-buyer_deal-1.json")), Path(".market/test-state"))
        self.assertEqual(state_dir_for(Path(".market/shops.json")), Path(".market/runtime-state"))

    def test_actual_receipt_is_returned_and_memory_uses_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shops = root / "shops.json"
            shops.write_text(json.dumps([{"name": "Good", "url": "http://good.test", "reviews": []}]))
            state = root / "state"
            with patch.dict(os.environ, {"AX_STATE_DIR": str(state)}):
                result = run(shops, "lamp", 120, buyer_factory=FakeBuyer)
            self.assertEqual(result["receipt"]["task"], "task-1")
            memory = json.loads((state / "buyer-memory.json").read_text())
            self.assertEqual(memory["shops"]["Good"]["agreements"], 1)

    def test_paid_total_counts_paid_records_and_ignores_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            payments = Path(tmp) / "payments.jsonl"
            payments.write_text(
                "\n".join(
                    (
                        json.dumps({"price": 23_360, "paid": True, "ledger": 2_412_022}),
                        json.dumps({"price": 10_000, "paid": False, "reason": "certificate"}),
                        json.dumps({"price": 7_000, "paid": True, "reason": "confirmation pending"}),
                    )
                ) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(paid_total(payments), 30_360)

    def test_paid_total_fails_closed_on_malformed_paid_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            payments = Path(tmp) / "payments.jsonl"
            payments.write_text(json.dumps({"price": "100", "paid": True}) + "\n", encoding="utf-8")
            with self.assertRaises(MarketBuyerError):
                paid_total(payments)

    def test_batch_spend_cap_is_optional_and_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(batch_spend_cap())
        with patch.dict(os.environ, {"BUYER_BATCH_SPEND_CAP": "500000"}, clear=True):
            self.assertEqual(batch_spend_cap(), 500_000)
        with patch.dict(os.environ, {"BUYER_BATCH_SPEND_CAP": "0"}, clear=True):
            with self.assertRaises(MarketBuyerError):
                batch_spend_cap()

    def test_per_order_cap_is_optional_and_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(per_order_spend_cap())
        with patch.dict(os.environ, {"BUYER_PER_ORDER_CAP": "25000"}, clear=True):
            self.assertEqual(per_order_spend_cap(), 25_000)
        with patch.dict(os.environ, {"BUYER_PER_ORDER_CAP": "bad"}, clear=True):
            with self.assertRaises(MarketBuyerError):
                per_order_spend_cap()

    def test_signed_c_grade_is_rejected_before_acp(self):
        self.assertEqual(
            _receipt_precheck({"item": "G1", "price": 100, "claim": "C"}, {"G1"}, 200),
            "signed receipt claims disallowed grade C",
        )
        self.assertIsNone(_receipt_precheck({"item": "G1", "price": 100, "claim": "A"}, {"G1"}, 200))
        self.assertEqual(
            _receipt_precheck(
                {"item": "G1", "price": 100, "claim": "A", "warranty_hours": 73},
                {"G1"}, 200, {"kind": "goods"},
            ),
            "signed receipt has invalid warranty hours",
        )
        self.assertEqual(
            _receipt_precheck(
                {"item": "G1", "price": 100, "claim": "A", "warranty_hours": 0},
                {"G1"}, 200, {"kind": "goods"},
            ),
            "signed receipt does not cover the six-hour goods break window",
        )
        self.assertIsNone(_receipt_precheck(
            {"item": "G1", "price": 100, "claim": "A", "warranty_hours": 6},
            {"G1"}, 200, {"kind": "goods"},
        ))

    def test_accept_policy_covers_food_life_and_goods_break_window(self):
        self.assertEqual(
            _accept_policy({"kind": "food", "use_within_hours": 72}),
            {"cert": True, "grades": ["A", "B"], "min_hours_left": 72},
        )
        self.assertEqual(
            _accept_policy({"kind": "goods"}),
            {"cert": True, "grades": ["A", "B"], "warranty_hours": 6},
        )

    def test_saved_snapshot_requires_both_official_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            payments = Path(tmp) / "payments.jsonl"
            payments.write_text(json.dumps({"paid": True, "ledger": 1, "price": 10}) + "\n")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(current_saved_state(payments), SavedState(0.0, 1))
            with patch.dict(os.environ, {"BUYER_SAVED_RATE": "0.0184"}, clear=True):
                with self.assertRaises(MarketBuyerError):
                    current_saved_state(payments)
            with patch.dict(os.environ, {
                "BUYER_SAVED_RATE": "0.0184", "BUYER_SETTLED_DEALS": "18",
            }, clear=True):
                self.assertEqual(current_saved_state(payments), SavedState(0.0184, 18))

    def test_settlement_history_uses_only_observed_paid_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            payments = Path(tmp) / "payments.jsonl"
            payments.write_text("\n".join((
                json.dumps({"shop": "https://s.test", "paid": True}),
                json.dumps({"shop": "https://s.test", "paid": False}),
                json.dumps({"shop": "https://s.test", "reason": "ambiguous"}),
            )) + "\n")
            history = settlement_history(payments)["https://s.test"]
            self.assertEqual(history, SettlementHistory(1, 1))
            self.assertAlmostEqual(settlement_probability(history), 0.5)

    def test_definitive_stockout_pair_is_not_selected_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            payments = Path(tmp) / "payments.jsonl"
            payments.write_text(json.dumps({
                "shop": "https://stock.test", "item": "G1", "paid": False,
                "reason": 'checkout refused: 409 {"error":"unit is not in stock"}',
            }) + "\n")
            blocked = stockout_pairs(payments)
            shop = Shop("stock", "https://stock.test", (), (
                {"sku": "G1", "qty": 1}, {"sku": "G2", "qty": 1},
            ))
            catalog = {"G1": {"list_price": 10}, "G2": {"list_price": 20}}
            self.assertEqual(_candidate_offer(shop, {"G1", "G2"}, catalog, blocked)[0], "G2")


if __name__ == "__main__":
    unittest.main()
