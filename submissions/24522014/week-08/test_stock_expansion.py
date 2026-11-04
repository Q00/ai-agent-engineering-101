"""Stock can grow to 40, new SKUs and categories come first, and the cash and food guards are unchanged."""

import time
import unittest
from unittest.mock import patch

import restock_capacity
import restock_policy as rp

RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 40, "max_proven_units_per_sku": 3, "max_units_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}
HOT = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 100}
NOW = 1793800000.0


def product(category, kind="goods", price=100):
    return {"category": category, "kind": kind, "list_price": price}


def demand(catalog):
    return [{"category": c} for c in {v["category"] for v in catalog.values()}]


def inventory(units=(), balance=100000000):
    return {"round": 3, "balance": balance, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": list(units)}


def unit(sku, category="a"):
    return {"sku": sku, "cost": 50, "grade": "A", "kind": "goods", "category": category, "supplied_at": NOW}


class Plan(unittest.TestCase):
    def plan(self, inv, catalog, intel, limits=LIMITS, sales=None):
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan(inv, catalog, demand(catalog), sales or {}, 0, intelligence=intel, runtime=RUNTIME, now=NOW)

    def test_one_run_spreads_across_new_skus_before_deepening(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(8)}
        buys, _ = self.plan(inventory(), catalog, {k: HOT for k in catalog})
        self.assertEqual(len(buys), 6)
        self.assertTrue(all(b["qty"] == 1 for b in buys))

    def test_new_categories_come_before_a_stocked_one(self):
        catalog = {"a1": product("a"), "b1": product("b"), "c1": product("c")}
        have = [unit("a0", "a")]
        limits = dict(LIMITS, max_units_per_run=1)
        catalog["a0"] = product("a")
        buys, _ = self.plan(inventory(have), catalog, {k: HOT for k in catalog}, limits)
        self.assertIn(buys[0]["sku"], ("b1", "c1"))

    def test_depth_follows_when_the_range_is_covered(self):
        catalog = {"s1": product("c1"), "s2": product("c2")}
        buys, _ = self.plan(inventory(), catalog, {k: HOT for k in catalog})
        self.assertEqual({b["sku"]: b["qty"] for b in buys}, {"s1": 3, "s2": 3})

    def test_no_duplicate_lines_and_run_and_sku_limits_hold(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(3)}
        buys, _ = self.plan(inventory(), catalog, {k: HOT for k in catalog})
        self.assertEqual(len({b["sku"] for b in buys}), len(buys))
        self.assertLessEqual(sum(b["qty"] for b in buys), 6)
        self.assertTrue(all(b["qty"] <= 3 for b in buys))

    def test_total_never_passes_40(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(30)}
        have = [unit(f"h{i}") for i in range(38)]
        buys, _ = self.plan(inventory(have), catalog, {k: HOT for k in catalog})
        self.assertEqual(sum(b["qty"] for b in buys), 2)
        buys, why = self.plan(inventory([unit(f"h{i}") for i in range(40)]), catalog, {k: HOT for k in catalog})
        self.assertEqual((buys, why["reason"]), ([], "stock_or_cash_limit"))

    def test_food_stays_out(self):
        catalog = {"f1": product("food", kind="food"), "g1": product("goods")}
        buys, _ = self.plan(inventory(), catalog, {k: HOT for k in catalog})
        self.assertEqual({b["sku"] for b in buys}, {"g1"})

    def test_above_the_old_cap_only_skus_with_evidence_are_added(self):
        catalog = {"unknown": product("c2")}
        have = [unit(f"h{i}") for i in range(16)]
        held = {row["sku"]: HOT for row in have}
        self.assertEqual(self.plan(inventory(have), catalog, held)[0], [])
        self.assertEqual(self.plan(inventory(have[:15]), catalog, held)[0], [{"sku": "unknown", "qty": 1}])
        catalog["known"] = product("c1")
        self.assertEqual(self.plan(inventory(have), catalog, dict(held, known=HOT))[0], [{"sku": "known", "qty": 3}])

    def test_cash_reserve_and_run_budget_still_bind(self):
        catalog = {f"s{i}": product(f"c{i}", price=100000) for i in range(8)}
        buys, why = self.plan(inventory(balance=4000000), catalog, {k: HOT for k in catalog})
        spent = sum(b["qty"] * 75000 * 1.07 for b in buys)
        self.assertLessEqual(spent, why["budget"] + 1)
        self.assertLessEqual(why["budget"], 4000000 * .05)
        self.assertEqual(self.plan(inventory(balance=900000), catalog, {k: HOT for k in catalog})[0], [])

    def test_paused_or_closed_window_buys_nothing(self):
        catalog = {"s1": product("c1")}
        closed = dict(inventory(), restock={"open": False})
        self.assertEqual(self.plan(closed, catalog, {"s1": HOT})[1]["reason"], "market_restock_window_closed")


class Bounds(unittest.TestCase):
    def test_settings_load_and_values_past_the_bounds_are_refused(self):
        settings = restock_capacity.config()
        self.assertEqual((settings["max_total_units"], settings["max_proven_units_per_sku"], settings["max_units_per_run"], settings["max_unproven_units"]), (40, 3, 6, 2))
        for key, value in (("max_total_units", 41), ("max_proven_units_per_sku", 4), ("max_units_per_run", 7), ("max_unproven_units", 3)):
            with self.subTest(key=key), patch.object(restock_capacity, "read", return_value=dict(settings, **{key: value})):
                with self.assertRaises(ValueError):
                    restock_capacity.config()


if __name__ == "__main__":
    unittest.main()
