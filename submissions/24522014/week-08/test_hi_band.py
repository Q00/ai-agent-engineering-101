"""Shop b (hi_band_slots.json): up to `slots` units of 1M+ goods get places of their own, outside the total cap, the run's units and
its lines, when the SKU has our own profitable completed sale in the last 24 h (that evidence stands in for the market median, as in
proposal 2; a surged SKU keeps the median test and its one-unit rule). They pay from the same run budget at worst cost. Off, or with
no qualifying SKU, the plan is exactly today's."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import hi_band
import median_bypass as mb
import restock_policy as rp

RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 10, "max_proven_units_per_sku": 3, "max_units_per_run": 4, "max_lines_per_run": 2, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}
NOW = 1793800000.0
HI_LIST = 2000000
# Proven by the public sample; the median of the 1M+ SKU fails today's test (.7575 of list), the cheap ones pass it.
CHEAP = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 95}
HI_LOW = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 1370000}


def catalog():
    out = {f"c{i}": {"category": "cat", "kind": "goods", "list_price": 100} for i in range(6)}
    out["H"] = {"category": "cat", "kind": "goods", "list_price": HI_LIST}
    out["H2"] = {"category": "cat", "kind": "goods", "list_price": HI_LIST}
    return out


def unit(sku, cost=75):
    return {"sku": sku, "cost": cost, "grade": "A", "kind": "goods", "category": "cat", "supplied_at": NOW}


def inventory(units=(), surge=None, balance=1000000000):
    return {"round": 3, "balance": balance, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": list(units), "surge": surge or []}


BAND = {"slots": 3, "min_list_price": 1000000, "sales": {"H": 2}, "max_worst_cost_total": 12000000}


class Plan(unittest.TestCase):
    def plan(self, inv, band=None, limits=LIMITS, intel=None):
        cat = catalog()
        intelligence = intel or {**{k: CHEAP for k in cat if k.startswith("c")}, "H": HI_LOW, "H2": HI_LOW}
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan(inv, cat, [{"category": "cat"}], {}, 0, intelligence=intelligence, runtime=RUNTIME, now=NOW, hi_band=band)

    def test_off_or_empty_is_exactly_today(self):
        for inv in (inventory(), inventory([unit("c0")] * 3 + [unit("H", 1500000)])):
            today = self.plan(inv)
            for band in (None, {**BAND, "sales": {}}):
                with self.subTest(band=band):
                    self.assertEqual(self.plan(inv, band), today)

    def test_today_the_1m_sku_fails_the_median_test(self):
        buys, _ = self.plan(inventory())
        self.assertNotIn("H", {b["sku"] for b in buys})

    def test_a_qualifying_1m_sku_gets_its_own_places_outside_lines_and_run_units(self):
        buys, why = self.plan(inventory(), BAND)
        got = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual(got.get("H"), 3)
        self.assertEqual(sum(q for s, q in got.items() if s != "H"), 4)          # the run's 4 units, untouched
        self.assertEqual(len([s for s in got if s != "H"]), 2)                   # and its 2 lines
        self.assertEqual(why["hi_band"]["planned"], 3)
        self.assertEqual(why["planned_units"], 7)

    def test_the_places_exist_even_when_the_total_cap_is_full(self):
        full = inventory([unit(f"c{i % 6}") for i in range(10)])
        today, why_today = self.plan(full)
        self.assertEqual((today, why_today["reason"]), ([], "stock_or_cash_limit"))
        buys, _ = self.plan(full, BAND)
        self.assertEqual(buys, [{"sku": "H", "qty": 3}])

    def test_held_1m_units_use_the_places_first(self):
        buys, _ = self.plan(inventory([unit("H", 1500000)] * 2), BAND)
        self.assertEqual({b["sku"]: b["qty"] for b in buys}.get("H"), 1)
        buys, _ = self.plan(inventory([unit("H2", 1500000)] * 3), BAND)
        self.assertNotIn("H", {b["sku"] for b in buys})

    def test_held_1m_units_inside_the_places_do_not_count_toward_the_total_cap(self):
        inv = inventory([unit(f"c{i % 6}") for i in range(8)] + [unit("H2", 1500000)] * 2)
        _, why = self.plan(inv)
        self.assertEqual(why["reason"], "stock_or_cash_limit")
        buys, _ = self.plan(inv, {**BAND, "sales": {"H": 2}})
        got = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual(got.get("H"), 1)
        self.assertEqual(sum(q for s, q in got.items() if s != "H"), 2)          # 8 counted of 10

    def test_only_skus_with_our_recent_sales_qualify(self):
        buys, _ = self.plan(inventory(), {**BAND, "sales": {"H2": 1}})
        got = {b["sku"]: b["qty"] for b in buys}
        self.assertNotIn("H", got)
        self.assertEqual(got.get("H2"), 3)

    def test_the_run_budget_bounds_worst_cost(self):
        # Budget = 5% of the balance; one unit's worst cost plus a week of storage is 1,605,000.
        buys, _ = self.plan(inventory(balance=34000000), BAND)
        self.assertEqual({b["sku"]: b["qty"] for b in buys}.get("H"), 1)
        buys, _ = self.plan(inventory(balance=30000000), BAND)
        self.assertNotIn("H", {b["sku"] for b in buys})

    def test_a_surged_sku_keeps_the_median_test_and_one_unit(self):
        buys, _ = self.plan(inventory(surge=[{"sku": "H", "factor": 1.1}]), BAND)
        self.assertNotIn("H", {b["sku"] for b in buys})
        good = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 1900000}
        intel = {**{k: CHEAP for k in catalog() if k.startswith("c")}, "H": good, "H2": HI_LOW}
        buys, _ = self.plan(inventory(surge=[{"sku": "H", "factor": 1.1}]), BAND, intel=intel)
        self.assertEqual({b["sku"]: b["qty"] for b in buys}.get("H"), 1)

    def test_the_worst_cost_of_held_and_planned_1m_stock_stays_under_its_cap(self):
        # Worst cost of H is 1,500,000 a unit. A 4,000,000 cap leaves room for 2; one held H2 (1,500,000) leaves room for 1.
        buys, why = self.plan(inventory(), {**BAND, "max_worst_cost_total": 4000000})
        self.assertEqual({b["sku"]: b["qty"] for b in buys}.get("H"), 2)
        self.assertEqual(why["hi_band"]["worst_cost_total"], 3000000)
        buys, _ = self.plan(inventory([unit("H2", 1500000)]), {**BAND, "max_worst_cost_total": 4000000})
        self.assertEqual({b["sku"]: b["qty"] for b in buys}.get("H"), 1)
        buys, _ = self.plan(inventory([unit("H2", 1500000)] * 3), {**BAND, "slots": 10, "max_worst_cost_total": 4000000})
        self.assertNotIn("H", {b["sku"] for b in buys})

    def test_the_per_sku_limit_holds(self):
        buys, _ = self.plan(inventory([unit("H", 1500000)]), {**BAND, "slots": 10})
        self.assertEqual({b["sku"]: b["qty"] for b in buys}.get("H"), 2)


class WiderBounds(unittest.TestCase):
    """Up to 20 places and a 24M worst-cost cap: the places, the per-SKU limit and the cap all still bind."""
    PUBLIC = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5}

    def plan(self, list_price, cap):
        cat = {f"H{i}": {"category": "cat", "kind": "goods", "list_price": list_price} for i in range(8)}
        # A median under the worst cost, so the ordinary pass skips them and only the band's places buy.
        intel = {k: dict(self.PUBLIC, median_price=int(list_price * .7)) for k in cat}
        band = {"slots": 20, "min_list_price": 1000000, "sales": {k: 2 for k in cat}, "max_worst_cost_total": cap}
        with patch.object(rp, "capacity_config", return_value=LIMITS):
            return rp.plan(inventory(), cat, [{"category": "cat"}], {}, 0, intelligence=intel, runtime=RUNTIME, now=NOW, hi_band=band)

    def test_twenty_places_fill_when_the_cap_has_room(self):
        buys, why = self.plan(1000000, 24000000)
        self.assertEqual(sum(b["qty"] for b in buys), 20)
        self.assertTrue(all(b["qty"] <= 3 for b in buys))
        self.assertEqual((why["hi_band"]["planned"], why["hi_band"]["worst_cost_total"]), (20, 15000000))

    def test_the_24m_cap_binds_before_twenty_places(self):
        buys, why = self.plan(2000000, 24000000)
        self.assertEqual(sum(b["qty"] for b in buys), 16)
        self.assertEqual(why["hi_band"]["worst_cost_total"], 24000000)

    def test_a_12m_cap_still_holds_with_twenty_places(self):
        buys, why = self.plan(2000000, 12000000)
        self.assertEqual(sum(b["qty"] for b in buys), 8)
        self.assertEqual(why["hi_band"]["worst_cost_total"], 12000000)


class Settings(unittest.TestCase):
    def test_off_unless_valid(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "h.json"
            good = {"enabled": True, "slots": 10, "min_list_price": 1000000, "min_own_sales_24h": 1, "min_own_sales_window_hours": 72, "max_worst_cost_total": 12000000}
            path.write_text(json.dumps(good), encoding="utf-8")
            self.assertEqual(hi_band.settings(path), {"slots": 10, "min_list_price": 1000000, "min_own_sales_24h": 1, "window_hours": 72, "max_worst_cost_total": 12000000})
            older = {k: v for k, v in good.items() if k != "min_own_sales_window_hours"}
            path.write_text(json.dumps(older), encoding="utf-8")
            self.assertEqual(hi_band.settings(path)["window_hours"], 24)
            # Bounds raised on the user's approval: up to 20 slots and a 24M cap; today's 10 and 12M stay valid.
            for wider in ({**good, "slots": 20, "max_worst_cost_total": 24000000}, {**good, "slots": 11, "max_worst_cost_total": 12000001}):
                path.write_text(json.dumps(wider), encoding="utf-8")
                got = hi_band.settings(path)
                self.assertEqual((got["slots"], got["max_worst_cost_total"]), (wider["slots"], wider["max_worst_cost_total"]))
            for bad in ({**good, "enabled": "true"}, {**good, "enabled": False}, {**good, "slots": 21}, {**good, "slots": 0}, {**good, "min_list_price": 999999},
                        {**good, "min_own_sales_24h": 0}, {**good, "slots": 10.0}, {**good, "min_own_sales_window_hours": 73}, {**good, "min_own_sales_window_hours": 23},
                        {**good, "max_worst_cost_total": 24000001}, {k: v for k, v in good.items() if k != "max_worst_cost_total"}):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNone(hi_band.settings(path), bad)
            self.assertIsNone(hi_band.settings(Path(folder) / "missing.json"))

    def test_the_shipped_setting(self):
        value = json.loads(hi_band.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual((value["slots"], value["min_list_price"], value["min_own_sales_24h"], value["min_own_sales_window_hours"], value["max_worst_cost_total"]), (20, 1000000, 1, 72, 24000000))
        # Installed off 12:06:21 KST 11-09 (cap and window 12:15:31); switched on in its own commit after the full suite.
        self.assertIs(value["enabled"], True)

    def test_for_run_keeps_1m_goods_with_profitable_recent_sales(self):
        counts = {"H": (2, 500), "H2": (1, -10), "c0": (5, 100), "H3": (1, 1)}
        cat = {**catalog(), "H3": {"category": "cat", "kind": "food", "list_price": HI_LIST}}
        chosen = {"slots": 10, "min_list_price": 1000000, "min_own_sales_24h": 1, "window_hours": 72, "max_worst_cost_total": 12000000}
        with patch.object(hi_band, "settings", return_value=chosen), patch.object(mb, "confirmed_counts", return_value=counts) as counted:
            self.assertEqual(hi_band.for_run(Path("."), NOW, cat), {"slots": 10, "min_list_price": 1000000, "sales": {"H": 2}, "max_worst_cost_total": 12000000})
        self.assertEqual(counted.call_args.kwargs.get("window"), 72 * 3600)
        with patch.object(hi_band, "settings", return_value=None):
            self.assertIsNone(hi_band.for_run(Path("."), NOW, cat))
        with patch.object(hi_band, "settings", return_value=chosen), patch.object(mb, "confirmed_counts", side_effect=OSError("x")):
            self.assertIsNone(hi_band.for_run(Path("."), NOW, cat))

    def test_the_count_window_is_a_parameter_and_the_bypass_keeps_24_h(self):
        import inspect
        self.assertEqual(inspect.signature(mb.confirmed_counts).parameters["window"].default, 86400)
        with patch.object(mb, "confirmed_counts", return_value={}) as counted:
            mb.confirmed_skus(Path("."), NOW)
        self.assertNotIn("window", counted.call_args.kwargs)

    def test_the_bypass_set_is_unchanged_by_the_shared_count(self):
        with patch.object(mb, "confirmed_counts", return_value={"a": (3, 1), "b": (2, 5), "c": (4, 0)}):
            self.assertEqual(mb.confirmed_skus(Path("."), NOW), {"a"})
        with patch.object(mb, "confirmed_counts", return_value=None):
            self.assertEqual(mb.confirmed_skus(Path("."), NOW), set())


if __name__ == "__main__":
    unittest.main()
