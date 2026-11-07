"""Proven goods gain bounded depth; fresh, low-cost food remains one-unit pilots."""
from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from adaptive_stock import candidates, depth_cap, prepare_control, remaining_credit

PROVEN = {"mature": 3, "net_estimate": 120000, "speed_score": 2,
          "unknown": 0, "refunded": 0, "pending": 0}


class AdaptiveGrowthMixTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.cfg = {"cash_reserve_percent": 40, "max_units_per_sku": 2, "min_mature_sales": 3,
            "max_run_cost_percent": 60, "max_sku_cost_percent": 40,
            "max_category_cost_percent": 60, "max_food_cost_percent": 10,
            "focused_goods": {"enabled": True, "primary_skus": ["G8095ddd6"],
                "secondary_skus": ["G03d0199f"], "max_secondary_sku_cost_percent": 40},
            "adaptive_goods": {"enabled": True, "max_candidates": 12, "max_retained_skus": 32,
                "max_units_per_run": 4, "low_stock_units": 12, "proven_max_units_per_sku": 2,
                "minimum_public_sales": 3, "minimum_public_buyers": 2,
                "minimum_public_shops": 2, "max_surge_factor": 1.10,
                "food_pilots": {"enabled": True, "max_candidates": 4, "max_units_per_run": 2,
                    "max_list_price": 100000, "min_shelf_hours": 72,
                    "minimum_public_sales": 5, "minimum_public_buyers": 3}}}
        self.inv = {"round": 3, "balance": 10000000, "start": 5000000,
                    "injected": 0, "units": [], "surge": []}
        self.catalog, self.metrics = {}, {}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [],
            "market_stock": {"round": 3, "observed_at": self.now, "by_sku": {},
                "shop_names_by_url": {f"https://shop-{i}.invalid": f"shop-{i}" for i in range(3)}}}
        self.add_product("G00000001")
        self.add_product("F00000001", kind="food")
        for target in ("harness.decide", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("No model/network allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def add_product(self, sku, *, kind="goods", price=None):
        price = price if price is not None else 20000 if kind == "food" else 200000
        self.catalog[sku] = {"name": f"Synthetic {sku}", "kind": kind, "category": kind,
                             "list_price": price, **({"shelf_hours": 72} if kind == "food" else {})}
        count = 5 if kind == "food" else 3
        for j in range(count):
            self.public["events"].append({"seq": len(self.public["events"]) + 1,
                "ts": self.now - 30, "kind": "settle", "sku": sku,
                "buyer": f"buyer-{j % 3}", "shop": f"https://shop-{j % 3}.invalid",
                "price": int(price * .75), "grade": "A", "warranty_hours": 0 if kind == "food" else 6})
        self.public["market_stock"]["by_sku"][sku] = {"units": 10, "shops": 3}

    def held(self, sku, count=1):
        product = self.catalog[sku]
        return [{"unit": f"held-{sku}-{i}", "sku": sku, "cost": 10000,
                 "category": product["category"], "kind": product["kind"]} for i in range(count)]

    def pool(self, **kw):
        return candidates(self.inv, self.catalog, {"goods", "food"}, self.metrics,
                          self.cfg, self.now, self.public, **kw)

    def test_proven_goods_can_hold_two_and_pending_warranty_does_not_erase_mature_profit(self):
        sku = "G00000001"
        self.inv["units"] = self.held(sku)
        self.assertNotIn(sku, self.pool())
        self.metrics[sku] = {**PROVEN, "pending": 10}
        row = self.pool()[sku]
        self.assertEqual(row["cap"], 2)
        self.assertEqual(row["evidence"]["depth_cap"], 2)
        self.assertEqual(row["evidence"]["own_mature"], 3)
        self.assertEqual(row["evidence"]["own_net_estimate"], 120000)
        self.inv["units"] = self.held(sku, 2)
        self.assertNotIn(sku, self.pool())

    def test_no_automatic_depth_for_pending_only_incomplete_or_refunded_goods(self):
        sku = "G00000001"
        self.inv["units"] = self.held(sku)
        for changes in ({"mature": 0, "pending": 10}, {"mature": 2}, {"net_estimate": 0},
                        {"speed_score": 0}, {"unknown": 1}, {"refunded": 1}):
            self.metrics[sku] = {**PROVEN, **changes}
            with self.subTest(changes=changes):
                self.assertNotIn(sku, self.pool())
                self.assertEqual(depth_cap(self.metrics[sku], self.cfg), 1)
        self.metrics[sku] = deepcopy(PROVEN)
        self.assertNotIn(sku, self.pool(paused={sku}))
        self.cfg["max_units_per_sku"] = 1
        self.assertEqual(depth_cap(PROVEN, self.cfg), 1)

    def test_legacy_optional_settings_keep_one_unit_goods_and_no_food(self):
        self.cfg["adaptive_goods"].pop("proven_max_units_per_sku")
        self.cfg["adaptive_goods"].pop("food_pilots")
        self.metrics["G00000001"] = deepcopy(PROVEN)
        self.assertEqual(depth_cap(PROVEN, self.cfg), 1)
        self.assertEqual(set(self.pool()), {"G00000001"})
        self.inv["units"] = self.held("G00000001")
        self.assertEqual(self.pool(), {})

    def test_proven_goods_precede_more_expensive_unproven_goods(self):
        self.add_product("G00000002", price=600000)
        self.metrics["G00000001"] = deepcopy(PROVEN)
        goods = [sku for sku, row in self.pool().items() if row["product"]["kind"] == "goods"]
        self.assertEqual(goods, ["G00000001", "G00000002"])

    def test_credit_capacity_is_bounded_and_does_not_erase_unconfirmed_history(self):
        row = {"planned_qty": 3, "settled_qty": 2, "retry_credit": 0,
               "last_ledger": 30, "observed_at_plan": 3}
        self.assertEqual(remaining_credit(row), 0)
        self.assertEqual(remaining_credit(row, 2), 1)
        self.assertEqual(remaining_credit({**row, "planned_qty": 4}, 2), 0)
        self.assertEqual(remaining_credit({**row, "settled_qty": 100}, 2), 2)
        self.assertEqual(remaining_credit({**row, "retry_credit": 1}, 2), 2)
        for capacity in (True, False, 0, 3, 2.0, "2"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                remaining_credit(row, capacity)
        controls = {"G00000001": deepcopy(row)}
        before = deepcopy(controls)
        result = prepare_control(controls, {"sales": []}, {}, self.cfg["adaptive_goods"],
            lifecycle_stats={"G00000001": {"unresolved_cost": 10000}}, held={})
        self.assertEqual(result, before)
        self.assertEqual(controls, before)

    def test_food_is_one_unit_and_catalog_shelf_life_is_not_actual_expiry(self):
        sku = "F00000001"
        self.metrics[sku] = deepcopy(PROVEN)
        row = self.pool()[sku]
        self.assertEqual(row["cap"], 1)
        self.assertEqual(depth_cap(PROVEN, self.cfg, "food"), 1)
        self.assertEqual(row["evidence"]["catalog_shelf_hours"], 72)
        self.assertIn("after delivery", row["evidence"]["shelf_life_basis"])
        self.assertNotIn("expires_at", row["product"])
        self.inv["units"] = self.held(sku)
        self.assertNotIn(sku, self.pool())

    def test_food_requires_five_recent_sales_three_buyers_and_two_named_shops(self):
        sku = "F00000001"
        original = deepcopy(self.public)
        for variant in ("four_sales", "two_buyers", "one_shop_name", "stale", "no_events"):
            self.public = deepcopy(original)
            food = [row for row in self.public["events"] if row["sku"] == sku]
            if variant == "four_sales": self.public["events"].remove(food[-1])
            if variant == "two_buyers":
                for i, row in enumerate(food): row["buyer"] = f"buyer-{i % 2}"
            if variant == "one_shop_name":
                self.public["market_stock"]["shop_names_by_url"] = {
                    f"https://shop-{i}.invalid": "same-registered-shop" for i in range(3)}
            if variant == "stale": self.public["updated_at"] -= 3601
            if variant == "no_events": self.public["events"] = []
            with self.subTest(variant=variant):
                self.assertNotIn(sku, self.pool())
        self.public = original
        self.assertIn(sku, self.pool())

    def test_food_rejects_unknown_short_shelf_life_expensive_goods_surge_and_local_risk(self):
        sku = "F00000001"
        original = deepcopy(self.catalog[sku])
        for shelf in (None, True, "72", float("nan"), float("inf"), 71.99):
            self.catalog[sku] = {**original, "shelf_hours": shelf}
            with self.subTest(shelf=shelf):
                self.assertNotIn(sku, self.pool())
        self.catalog[sku] = {**original, "list_price": 100001}
        self.assertNotIn(sku, self.pool())
        self.catalog[sku] = {**original, "list_price": 100000}
        self.assertIn(sku, self.pool())
        self.inv["surge"] = [{"sku": sku, "factor": 1.11, "units": 100}]
        self.assertNotIn(sku, self.pool())
        self.inv["surge"] = []
        for metric in ({"unknown": 1}, {"refunded": 1}, {"mature": 1, "net_estimate": -1}):
            self.metrics[sku] = metric
            with self.subTest(metric=metric):
                self.assertNotIn(sku, self.pool())
        self.metrics = {}
        self.assertNotIn(sku, self.pool(paused={sku}))

    def test_mixed_pool_is_bounded_and_absent_food_does_not_shrink_goods(self):
        for i in range(2, 15): self.add_product(f"G{i:08x}")
        for i in range(2, 7): self.add_product(f"F{i:08x}", kind="food")
        pool = self.pool()
        self.assertEqual(len(pool), 12)
        self.assertEqual(sum(row["product"]["kind"] == "food" for row in pool.values()), 4)
        self.public["events"] = [row for row in self.public["events"] if not row["sku"].startswith("F")]
        pool = self.pool()
        self.assertEqual(len(pool), 12)
        self.assertTrue(all(row["product"]["kind"] == "goods" for row in pool.values()))

    def test_food_ranks_demand_pressure_before_price_and_respects_fixed_food_cap(self):
        self.add_product("F00000002", kind="food", price=100000)
        self.public["market_stock"]["by_sku"]["F00000001"]["units"] = 1
        self.public["market_stock"]["by_sku"]["F00000002"]["units"] = 100
        foods = [sku for sku, row in self.pool().items() if row["product"]["kind"] == "food"]
        self.assertEqual(foods, ["F00000001", "F00000002"])
        self.inv["units"] = [{"unit": "food-budget", "sku": "Fffffffff", "cost": 500000,
                              "kind": "food", "category": "other-food"}]
        self.assertFalse(any(row["product"]["kind"] == "food" for row in self.pool().values()))


if __name__ == "__main__":
    unittest.main()
