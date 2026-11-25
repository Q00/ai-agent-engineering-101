"""Offline strategy fixtures: fresh price learning, bounded calls, unchanged checkout."""
from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from buyer_fast_selection import exploration_turn, value_choice
from buyer_policy import load_policy
from buyer_selection import shortlist
from market_buyer import choose_shop
import test_buyer_fast_selection as fast_fixtures
import test_buyer_value_memory as memory_fixtures
from buyer_value_memory import build_value_evidence
from test_buyer_selection import shop
from test_buyer_value_selection import history


class ValueShortcutTests(unittest.TestCase):
    def setUp(self):
        self.seed = next(f"v{i}" for i in range(100) if not exploration_turn(f"v{i}"))
        self.config = load_policy()
        self.order = {"round": 3, "order": self.seed, "kind": "goods", "acceptable": ["G00001"]}
        self.catalog = {"G00001": {"kind": "goods", "list_price": 100000}}
        self.shops = [shop("value", [5]), shop("other", [5])]
        self.values = {("value", "G00001"): history(60000)}
        self.group = self.values[("value", "G00001")]["groups"][0]
        self.group["age_seconds"] = 120

    def choose(self, *, values=None, config=None, order=None, seed=None, memory=None):
        values = self.values if values is None else values
        candidates = shortlist(self.shops, memory or [], seed=self.seed, round_number=3, value_evidence=values)
        return value_choice(candidates, values=values, budget=100000,
                            config=config or self.config, order=order or self.order,
                            catalog=self.catalog, seed=seed or self.seed)

    def test_repeated_good_value_selects_stock_without_purchase_authority(self):
        before = deepcopy(self.values)
        self.assertEqual(self.choose()["name"], "value")
        self.assertNotIn("price", self.choose())
        self.assertNotIn("receipt", self.choose())
        self.assertEqual(self.values, before)
        self.shops[0]["offers"][0]["sku"] = "G99999"
        self.assertIsNone(self.choose())

    def test_stale_thin_nonfinite_or_expensive_reference_needs_normal_comparison(self):
        for changes in ({"sample_count": 2}, {"sample_count": True}, {"age_seconds": 901},
                        {"age_seconds": -1}, {"age_seconds": float("nan")},
                        {"median_price": 65001}, {"median_price": True},
                        {"kind": "food"}, {"grade": "C"}, {"warranty_hours": 3},
                        {"warranty_hours": float("nan")}, {"source": "seller_claim"}):
            values = deepcopy(self.values)
            values[("value", "G00001")]["groups"][0].update(changes)
            with self.subTest(changes=changes):
                self.assertIsNone(self.choose(values=values))

    def test_public_medians_need_five_trades_and_three_buyers(self):
        self.group.update(source="public_ledger", sample_count=5, buyer_count=3)
        self.assertIn("public", self.choose()["reason"])
        for changes in ({"sample_count": 4}, {"buyer_count": 2}, {"buyer_count": True}):
            values = deepcopy(self.values)
            values[("value", "G00001")]["groups"][0].update(changes)
            self.assertIsNone(self.choose(values=values))

    def test_faults_reviews_exploration_and_disabled_cache_preserve_model_path(self):
        self.assertIsNone(self.choose(memory=[{"name": "value", "round": 3, "result": "receipt_mismatch"}]))
        explore = next(f"v{i}" for i in range(100) if exploration_turn(f"v{i}"))
        self.assertIsNone(self.choose(seed=explore))
        self.assertIsNone(self.choose(config={**self.config, "selection_cache_seconds": 0}))
        self.shops[0]["reviews"].append({"stars": 1})
        self.assertIsNone(self.choose())

    def test_lower_ratio_wins_not_nominal_price_or_shop_order(self):
        self.shops[1]["offers"][0]["sku"] = "G00002"
        self.catalog["G00002"] = {"kind": "goods", "list_price": 200000}
        # 70k/200k beats 60k/100k despite costing more in currency units.
        self.values[("other", "G00002")] = history(70000, 5)
        self.values[("other", "G00002")]["groups"][0]["age_seconds"] = 30
        self.assertEqual(self.choose()["name"], "other")
        self.shops.reverse()
        self.assertEqual(self.choose()["name"], "other")

    def test_food_requires_observed_usable_life_and_own_evidence(self):
        self.catalog["G00001"]["kind"] = "food"
        order = {**self.order, "kind": "food", "use_within_hours": 8}
        self.group.update(kind="food", grade="C", minimum_hours_left=9, warranty_hours=None)
        self.assertIsNotNone(self.choose(order=order))
        self.group["minimum_hours_left"] = 8.9
        self.assertIsNone(self.choose(order=order))
        self.group.update(minimum_hours_left=10, source="public_ledger", sample_count=5, buyer_count=3)
        self.assertIsNone(self.choose(order=order))

    def test_integrated_value_path_beats_cached_expensive_choice_without_model(self):
        with patch("market_buyer.build_value_evidence", return_value=self.values), \
                patch("market_buyer.recent_success_choice") as cached, \
                patch("market_buyer.decide") as model:
            chosen, reason = choose_shop(self.shops, [], item="chair", budget=100000,
                deadline=time.monotonic()+30, policy=self.config, order=self.order, catalog=self.catalog)
        self.assertEqual(chosen["name"], "value")
        self.assertEqual(chosen["price_reference"], self.values[("value", "G00001")]["groups"])
        self.assertIn("Repeated", reason)
        cached.assert_not_called()
        model.assert_not_called()

    def test_missing_proof_does_not_trust_registry_injected_history(self):
        self.shops[0]["offers"][0]["price_history"] = [deepcopy(self.group)]
        self.assertIsNone(self.choose(values={}))

    def test_expensive_past_success_no_longer_automatically_repeats(self):
        fixture = fast_fixtures.FastSelectionTests()
        fixture.setUp()
        for price in (72000, 72001, 80000):
            record = deepcopy(fixture.record)
            record["price"] = record["observed_receipt"]["price"] = price
            with self.subTest(price=price):
                self.assertEqual(fixture.cached(records=[record]) is not None, price <= 72000)

    def test_real_correlated_memory_drives_choice_and_refund_loses_eligibility(self):
        fixture = memory_fixtures.BuyerValueMemoryTests()
        fixture.setUp()
        try:
            rows = [fixture.learned(i) for i in range(1, 4)]
            source = shop("shop", [5])
            source["offers"][0]["sku"] = memory_fixtures.SKU
            def choose():
                values = build_value_evidence(rows, catalog=fixture.catalog, order=fixture.order,
                    budget=100000, config=self.config, now=fixture.now)
                candidates = shortlist([source], rows, seed=self.seed, round_number=3, value_evidence=values)
                return value_choice(candidates, values=values, budget=100000, config=self.config,
                    order=fixture.order, catalog=fixture.catalog, seed=self.seed)
            self.assertEqual(choose()["name"], "shop")
            rows[0]["refund_amount"] = 60000
            self.assertIsNone(choose())
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
