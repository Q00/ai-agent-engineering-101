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


class Depth(Plan):
    WIDE = dict(LIMITS, max_total_units=80, max_units_per_run=12, max_lines_per_run=6)

    def test_range_comes_first_and_depth_fills_the_units_within_the_lines(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(20)}
        buys, why = self.plan(inventory(), catalog, {k: HOT for k in catalog}, self.WIDE)
        self.assertEqual(len(buys), 6)
        self.assertEqual(sum(b["qty"] for b in buys), 12)
        self.assertTrue(all(1 <= b["qty"] <= 3 for b in buys))
        self.assertEqual(why["planned_units"], 12)

    def test_a_plan_respects_16_units_with_the_same_six_lines(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(20)}
        buys, why = self.plan(inventory(), catalog, {k: HOT for k in catalog}, dict(self.WIDE, max_units_per_run=16))
        self.assertEqual(len(buys), 6)
        self.assertEqual(sum(b["qty"] for b in buys), 16)
        self.assertTrue(all(1 <= b["qty"] <= 3 for b in buys))
        self.assertEqual(why["planned_units"], 16)

    def test_a_sku_without_bulk_evidence_stays_at_one(self):
        thin = {"distinct_buyers": 3, "settles_6h": 3, "distinct_buyers_6h": 3, "median_price": 100}
        catalog = {f"s{i}": product(f"c{i}") for i in range(20)}
        buys, _ = self.plan(inventory(), catalog, {k: thin for k in catalog}, self.WIDE)
        self.assertEqual((len(buys), sum(b["qty"] for b in buys)), (6, 6))

    def test_the_total_of_80_holds(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(20)}
        have = [unit(f"h{i}") for i in range(78)]
        buys, _ = self.plan(inventory(have), catalog, {k: HOT for k in catalog}, self.WIDE)
        self.assertEqual(sum(b["qty"] for b in buys), 2)
        buys, why = self.plan(inventory([unit(f"h{i}") for i in range(80)]), catalog, {k: HOT for k in catalog}, self.WIDE)
        self.assertEqual((buys, why["reason"]), ([], "stock_or_cash_limit"))

    def test_the_budget_still_binds_on_depth(self):
        catalog = {f"s{i}": product(f"c{i}", price=1000000) for i in range(20)}
        hot = dict(HOT, median_price=1000000)
        buys, why = self.plan(inventory(balance=100000000), catalog, {k: hot for k in catalog}, self.WIDE)
        self.assertLessEqual(sum(b["qty"] for b in buys)*750000*1.07, why["budget"]+1)
        self.assertLessEqual(len(buys), 6)

    def test_settings_without_lines_keep_a_line_for_each_unit(self):
        catalog = {f"s{i}": product(f"c{i}") for i in range(8)}
        buys, _ = self.plan(inventory(), catalog, {k: HOT for k in catalog})
        self.assertEqual((len(buys), sum(b["qty"] for b in buys)), (6, 6))


class Surge(Plan):
    WIDE = Depth.WIDE

    def surged(self, units=(), factor=1.2, **extra):
        return dict(inventory(units, **extra), surge=[{"sku": "hot", "units": 150, "factor": factor}])

    def test_a_surged_sku_is_not_deepened(self):
        catalog = {"hot": product("c1"), "s2": product("c2")}
        buys, _ = self.plan(self.surged(), catalog, {k: HOT for k in catalog}, self.WIDE)
        self.assertEqual({b["sku"]: b["qty"] for b in buys}, {"hot": 1, "s2": 3})

    def test_a_surged_sku_already_held_is_not_bought(self):
        catalog = {"hot": product("c1"), "s2": product("c2")}
        buys, _ = self.plan(self.surged([unit("hot", "c1")]), catalog, {k: HOT for k in catalog}, self.WIDE)
        self.assertNotIn("hot", {b["sku"] for b in buys})

    def test_the_market_price_must_clear_the_surged_cost(self):
        # Worst cost 75; times 1.2 is 90. A public median of 85 clears the plain cost and not the surged one.
        catalog = {"hot": product("c1")}
        intel = {"hot": dict(HOT, median_price=85)}
        self.assertEqual(self.plan(self.surged(), catalog, intel, self.WIDE)[0], [])
        self.assertEqual(self.plan(inventory(), catalog, intel, self.WIDE)[0], [{"sku": "hot", "qty": 3}])

    def test_the_budget_uses_the_surged_cost(self):
        catalog = {"hot": product("c1", price=1000000)}
        intel = {"hot": dict(HOT, median_price=1000000)}
        _, why = self.plan(self.surged(), catalog, intel, self.WIDE)
        self.assertEqual(round(why["estimated_max_cost"]), 900000)

    def test_a_broken_surge_list_changes_nothing(self):
        catalog = {"s1": product("c1")}
        for rows in (None, "x", ["x"], [{"sku": "s1"}], [{"sku": "s1", "factor": "2"}], [{"sku": "s1", "factor": float("inf")}], [{"sku": "s1", "factor": 1}]):
            buys, _ = self.plan(dict(inventory(), surge=rows if isinstance(rows, list) or rows is None else []), catalog, {"s1": HOT}, self.WIDE)
            self.assertEqual(buys, [{"sku": "s1", "qty": 3}], rows)


class Bounds(unittest.TestCase):
    def test_settings_load_and_values_past_the_bounds_are_refused(self):
        settings = restock_capacity.config()
        # 16 units and 8 lines a run from 11-13 about 11:2x KST (user, Orchestrator session: "8품목/16개 (권장)"); 12 and 6 before.
        self.assertEqual((settings["max_total_units"], settings["max_proven_units_per_sku"], settings["max_units_per_run"], settings["max_lines_per_run"], settings["max_unproven_units"]), (80, 3, 16, 8, 2))
        for key, value in (("max_total_units", 81), ("max_proven_units_per_sku", 4), ("max_units_per_run", 17), ("max_units_per_run", 0), ("max_lines_per_run", 11), ("max_lines_per_run", 0), ("max_lines_per_run", "6"), ("max_unproven_units", 3)):
            with self.subTest(key=key, value=value), patch.object(restock_capacity, "read", return_value=dict(settings, **{key: value})):
                with self.assertRaises(ValueError):
                    restock_capacity.config()

    def test_units_per_run_up_to_16_are_authorized(self):
        # The user approved raising the bound from 12 to 16 on 11-10; 12 stays valid and 17 is refused above.
        settings = restock_capacity.config()
        for value in (12, 13, 16):
            with self.subTest(value=value), patch.object(restock_capacity, "read", return_value=dict(settings, max_units_per_run=value)):
                self.assertEqual(restock_capacity.config()["max_units_per_run"], value)


if __name__ == "__main__":
    unittest.main()
