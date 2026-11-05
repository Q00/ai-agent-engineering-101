"""tools/stock.py rules (D1, D2, D4, D10, D14, L1, supply windows) and the restock hook's one-line output,
offline."""
import copy
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-restock-"))
sys.path.insert(0, str(W))

import policy  # noqa: E402
from tools import stock  # noqa: E402

POL = copy.deepcopy(policy.DEFAULTS)
END = POL["stock"]["end_ts"]
NOW = END - 10 * 86400


def cat(sku, leaf, price, kind="food", shelf=720):
    return sku, {"name": sku, "category": f"{'식품' if kind == 'food' else '가구'}>{leaf}", "list_price": price,
                 "kind": kind, "shelf_hours": shelf if kind == "food" else None}


def dem(leaf, median, kind="food", shelf=720):
    return {"category": f"{'식품' if kind == 'food' else '가구'}>{leaf}", "item": leaf, "kind": kind, "skus": 2,
            "median_price": median, "shelf_hours": shelf if kind == "food" else None}


CATALOG = dict([cat("F1a", "김치", 10000), cat("F1b", "김치", 14000), cat("F2", "쌀", 30000),
                cat("F3", "라면", 5000), cat("S1", "우유", 3000, shelf=96),
                cat("G1", "의자", 40000, "goods"), cat("G2", "침대", 300000, "goods")])
DEMAND = [dem("김치", 11000), dem("쌀", 30000), dem("라면", 5000), dem("우유", 3000, shelf=96),
          dem("의자", 40000, "goods"), dem("침대", 300000, "goods")]


def inv(balance=5000000, units=()):
    return {"balance": balance, "start": 5000000, "injected": 0, "bankruptcies_left": 2, "bankrupt_line": 500000,
            "storage_per_day": 0.01, "units": list(units)}


def unit(sku, cost, kind="food", supplied=NOW - 3600, grade="A"):
    return {"unit": f"u-{sku}-{cost}", "sku": sku, "category": CATALOG.get(sku, {}).get("category", "x>y"),
            "list_price": 0, "kind": kind, "cost": cost, "grade": grade, "supplied_at": supplied}


class PlanTest(unittest.TestCase):
    def test_bootstrap_long_shelf_food_first_then_cheap_goods(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, POL, NOW)
        skus = [b["sku"] for b in p["buy"]]
        self.assertEqual(skus, ["F1a", "F3", "F2", "G1"])            # 720 h food tier first, then goods
        self.assertTrue(all(b["qty"] == POL["stock"]["per_sku_qty"] for b in p["buy"]))
        self.assertEqual(p["excluded"]["우유"], "D2_short_shelf_wait")
        self.assertEqual(p["excluded"]["침대"], "D2_goods_over_max_list")
        self.assertEqual(p["headroom"], 1500000)                      # 30 % of 5,000,000, cash floor not binding
        self.assertEqual(p["worst_cost"], int(0.75 * 2 * (10000 + 30000 + 5000 + 40000)))
        self.assertIn("D1", p["rules"])

    def test_sku_nearest_median(self):
        p = stock.plan_restock(inv(), CATALOG, [dem("김치", 13000)], {}, POL, NOW)
        self.assertEqual(p["buy"], [{"sku": "F1b", "qty": 2}])

    def test_caps_use_the_a_grade_worst_cost(self):
        pol = copy.deepcopy(POL)
        pol["stock"]["per_sku_qty"] = 20
        # cash floor binds: headroom = 1,200,000 - 1,000,000 = 200,000; worst 0.75 x 100,000 = 75,000 a unit
        c = dict([cat("X", "한우", 100000), cat("Y", "두부", 20000)])
        p = stock.plan_restock(inv(1200000), c, [dem("한우", 100000), dem("두부", 20000)], {}, pol, NOW)
        self.assertEqual(p["headroom"], 200000)
        self.assertEqual(p["buy"][0], {"sku": "Y", "qty": 13})         # 13 x 15,000 = 195,000
        self.assertEqual(p["excluded"]["X"], "D1_cap")
        self.assertLessEqual(p["worst_cost"], p["headroom"])
        # inventory already at the 30 % share: nothing more
        p = stock.plan_restock(inv(3500000, [unit("Z", 1500000)]), c, [dem("두부", 20000)], {}, pol, NOW)
        self.assertEqual(p["headroom"], 0)
        self.assertEqual(p["buy"], [])

    def test_held_stock_counts_and_d4_stale_goods(self):
        units = [unit("F1a", 6000), unit("F2", 15000), unit("F2", 15000),
                 unit("G1", 20000, "goods", supplied=NOW - 6 * 86400)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, POL, NOW)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got.get("F1a"), 1)
        self.assertNotIn("F2", got)
        self.assertEqual(p["excluded"]["F2"], "stocked")
        self.assertEqual(p["excluded"]["G1"], "D4_stale_goods")
        self.assertIn("D4", p["rules"])

    def test_short_shelf_food_opens_after_enough_observations(self):
        s = {"buyer": {"category": {"우유": {"uwh": {"n": 10, "median": 20}}}}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertIn("S1", [b["sku"] for b in p["buy"]])

    def test_l1_priority_and_regular_categories(self):
        s = {"market": {"categories": {"라면": {"demand": 9, "supply": 0}, "쌀": {"demand": 9, "supply": 8}}}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(p["buy"][0]["sku"], "F3")                   # (9+1)/(0+1) beats (0+1)/(0+1) and 10/9
        s["seller"] = {"regular_cats": ["의자"]}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(p["buy"][0]["sku"], "G1")                   # regular customers' category first
        self.assertIn("L1.regular", p["rules"])
        pol = copy.deepcopy(POL)
        pol["stock"]["supply_demand_priority"] = False
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, pol, NOW)
        self.assertNotIn("L1", p["rules"])

    def test_d14_taper_and_stop(self):
        old = copy.deepcopy(POL)                                     # the schedule before Patch 3.3
        old["stock"].update(taper_h=72, goods_stop_h=24)
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 0.1}}, "regular_cats": ["라면"]}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, old, END - 30 * 3600)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got, {"F1a": 1, "F3": 1})                   # 0.1 x 30 x 0.5 = 1.5; regular keeps 1
        self.assertEqual(p["excluded"]["F2"], "D14_taper")
        self.assertIn("D14", p["rules"])
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, old, END - 20 * 3600)
        self.assertEqual(p["excluded"]["의자"], "D14_goods_stop")
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, old, END + 1)
        self.assertEqual(p["buy"], [])

    def test_patch_3_3_defaults_keep_buying_to_the_end(self):
        # stock counts at cost: taper_h 0 never tapers, goods stop only in the last goods_stop_h (6) hours
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, POL, END - 7 * 3600)
        self.assertEqual([b["sku"] for b in p["buy"]], ["F1a", "F3", "F2", "G1"])
        self.assertNotIn("D14", p["rules"])
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, POL, END - 5 * 3600)
        self.assertEqual((p["excluded"]["의자"], [b["sku"] for b in p["buy"]]), ("D14_goods_stop", ["F1a", "F3", "F2"]))
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, POL, END - 60)
        self.assertNotIn("D14_taper", p["excluded"].values())

    def test_line_and_qty_limits(self):
        c = dict(cat(f"K{i}", f"c{i}", 1000) for i in range(15))
        d = [dem(f"c{i}", 1000) for i in range(15)]
        pol = copy.deepcopy(POL)
        pol["stock"]["per_sku_qty"] = 20
        pol["stock"]["max_qty"] = 5
        p = stock.plan_restock(inv(), c, d, {}, pol, NOW)
        self.assertEqual(len(p["buy"]), 10)
        self.assertTrue(all(b["qty"] == 5 for b in p["buy"]))
        self.assertEqual(sum(1 for v in p["excluded"].values() if v == "max_lines"), 5)

    def test_fast_seller_covers_until_the_next_window(self):
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 12}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, POL, NOW)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got["F1a"], 7)                              # ceil(12 x 25/60 x 1.5) = 8, one held
        self.assertEqual(got["F3"], POL["stock"]["per_sku_qty"])     # no sales: per_sku_qty

    def test_cover_target_is_capped_by_max_hold_and_a_line_by_max_qty(self):
        pol = copy.deepcopy(POL)
        pol["stock"]["max_hold"] = 6
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 100}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)] * 2), CATALOG, DEMAND, s, pol, NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 4)    # holds at most 6
        # 56 x 25/60 x 1.5 = 35 wanted (under max_hold 40), held 0 -> one line of max_qty 20
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 56}}}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 20)  # ceil(35) wanted, 20 a line
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)] * 20), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 15)  # the next run tops up to 35
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 500}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)] * 30), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 10)  # max_hold 40

    def test_sold_out_picks_first(self):
        units = [unit("F1a", 6000), unit("F3", 3000)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, POL, NOW)
        self.assertEqual([b["sku"] for b in p["buy"]], ["F2", "G1", "F1a", "F3"])
        self.assertEqual(p["empty_picks"], 2)

    def test_bad_inputs_do_not_raise(self):
        self.assertEqual(stock.plan_restock({}, {}, [], {}, POL, NOW)["buy"], [])
        self.assertEqual(stock.plan_restock(None, None, None, None, POL, NOW)["buy"], [])


class OverpricedAndSurgeTest(unittest.TestCase):
    """Units whose cost floor is above list (pricing.overpriced) and inventory.json `surge`."""

    @staticmethod
    def dear(sku="G1", cost=39000, supplied=NOW - 3600):        # 39,000 x 1.08 > list 40,000
        return dict(unit(sku, cost, "goods", supplied=supplied), list_price=CATALOG[sku]["list_price"])

    def test_overpriced_units_do_not_count_as_stock(self):
        p = stock.plan_restock(inv(units=[self.dear(), self.dear()]), CATALOG, DEMAND, {}, POL, NOW)
        self.assertEqual(p["overpriced"], 2)
        self.assertIn({"sku": "G1", "qty": POL["stock"]["per_sku_qty"]}, p["buy"])
        self.assertEqual(p["empty_picks"], 4)                          # G1 counts as sold out
        cheap = dict(self.dear(cost=20000))
        p = stock.plan_restock(inv(units=[cheap, cheap]), CATALOG, DEMAND, {}, POL, NOW)
        self.assertEqual((p["overpriced"], p["excluded"]["G1"]), (0, "stocked"))

    def test_old_overpriced_unit_does_not_mark_the_sku_stale(self):
        units = [self.dear(supplied=NOW - 6 * 86400), self.dear(cost=20000)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, POL, NOW)
        self.assertNotIn("G1", p["excluded"])
        self.assertIn({"sku": "G1", "qty": 1}, p["buy"])               # the fresh unit is held, the old one is not
        self.assertNotIn("D4", p["rules"])

    def test_surge_skips_a_sku_whose_floor_would_pass_list(self):
        def plan(factor, mult=1.08):
            pol = copy.deepcopy(POL)
            pol["seller"]["floor_cost_mult"] = mult
            return stock.plan_restock(dict(inv(), surge=[{"sku": "G1", "name": "x", "units": 99, "factor": factor}]),
                                      CATALOG, DEMAND, {}, pol, NOW)
        self.assertEqual(plan(1.3)["excluded"]["G1"], "surge")        # 1.3 x 0.75 x 1.08 = 1.053
        self.assertIn("G1", [b["sku"] for b in plan(1.2)["buy"]])      # 0.972
        self.assertEqual(plan(1.2, mult=1.23)["excluded"]["G1"], "surge")   # 1.107: the cutoff follows the floor

    def test_no_surge_never_skips(self):
        pol = copy.deepcopy(POL)
        pol["stock"]["worst_cost_share"] = 1.0                       # 1.0 x 1.08 > 1 without any surge
        p = stock.plan_restock(dict(inv(), surge=[{"sku": "G1", "factor": 1.0}]), CATALOG, DEMAND, {}, pol, NOW)
        self.assertNotIn("surge", p["excluded"].values())
        self.assertIn("G1", [b["sku"] for b in p["buy"]])
        p = stock.plan_restock(dict(inv(), surge=[{"sku": "G1", "factor": 1.01}]), CATALOG, DEMAND, {}, pol, NOW)
        self.assertEqual(p["excluded"]["G1"], "surge")

    def test_surge_raises_the_worst_cost(self):
        pol = copy.deepcopy(POL)
        pol["stock"]["per_sku_qty"] = 20
        c = dict([cat("Y", "두부", 20000)])
        p = stock.plan_restock(dict(inv(1200000), surge=[{"sku": "Y", "factor": 1.05}]), c, [dem("두부", 20000)], {},
                               pol, NOW)
        self.assertEqual(p["buy"], [{"sku": "Y", "qty": 12}])         # 200,000 // (15,000 x 1.05); 13 without
        self.assertEqual(p["worst_cost"], 189000)

    def test_surge_rows(self):
        rows = [{"sku": "G1", "factor": "x"}, {"sku": "F1a", "factor": None}, "junk", {"factor": 1.5},
                {"sku": "F3", "factor": float("nan")}, {"sku": "F2", "factor": 1.1}]
        self.assertEqual(stock.surge_factors({"surge": rows}), {"F2": 1.1})
        for bad in (None, {}, {"surge": {"surge": rows}}, {"surge": "x"}):
            self.assertEqual(stock.surge_factors(bad), {})

    def test_focus_sku_surging_reports_the_reason(self):
        p = stock.plan_restock(dict(inv(), surge=[{"sku": "G2", "factor": 1.4}]), CATALOG, DEMAND, {},
                               focus_pol("G2"), NOW)
        self.assertEqual(p["excluded"]["G2"], "surge")
        self.assertNotIn("G2", [b["sku"] for b in p["buy"]])


def focus_pol(*skus, **st):
    pol = copy.deepcopy(POL)
    pol["stock"].update(focus_skus=list(skus), **st)
    return pol


class FocusTest(unittest.TestCase):
    """stock.focus_skus: the strategist's SKUs, stocked deeper and first."""

    def test_empty_focus_list_is_the_old_plan(self):
        for s in ({}, {"seller": {"skus": {"F1a": {"sales_per_h": 12}}}}):
            self.assertEqual(stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol(), NOW),
                             stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW))

    def test_focus_above_goods_max_list_and_not_the_pick(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("G2", "F1b"), NOW)
        skus = [b["sku"] for b in p["buy"]]
        self.assertEqual(skus[:2], ["F1b", "G2"])                     # focus first, then the old order
        self.assertEqual(skus[2:], ["F1a", "F3", "F2", "G1"])
        self.assertEqual(p["excluded"]["침대"], "D2_goods_over_max_list")    # the category itself stays out

    def test_focus_pick_is_one_candidate(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("F3"), NOW)
        self.assertEqual([b["sku"] for b in p["buy"]], ["F3", "F1a", "F2", "G1"])

    def test_focus_target_is_deeper(self):
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 12}, "F3": {"sales_per_h": 12}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, focus_pol("F1a"), NOW)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got["F1a"], 14)                             # ceil(12 x 25/60 x 1.5 x 2) = 15, one held
        self.assertEqual(got["F3"], 8)                               # not focus: ceil(7.5) = 8
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("F1a"), NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], POL["stock"]["per_sku_qty"])
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol("F1a", max_qty=10), NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 10)

    def test_focus_cap_share(self):
        # headroom 200,000 (cash floor binds); focus share 0.5 = 100,000; G1 worst 30,000 a unit, X 7,500
        c = dict(CATALOG, **dict([cat("X", "두부", 10000)]))
        s = {"seller": {"skus": {"X": {"sales_per_h": 60}}}}
        p = stock.plan_restock(inv(1200000), c, [dem("두부", 10000), dem("라면", 5000)], s,
                               focus_pol("G1", "X"), NOW)
        self.assertEqual(p["headroom"], 200000)
        self.assertEqual(p["excluded"]["G1"], "focus_cap")
        self.assertEqual(p["buy"][0], {"sku": "X", "qty": 13})          # 13 x 7,500 = 97,500 of 100,000
        self.assertEqual(p["buy"][1], {"sku": "F3", "qty": 2})          # non-focus: its own per_sku_qty
        # headroom 250,000; focus share 125,000 -> X 16 x 7,500 = 120,000; Y (not focus) gets the rest of the
        # headroom, 130,000 -> 17, not the headroom outside the focus share (125,000 -> 16) nor all of it (20)
        xy = dict([cat("X", "두부", 10000), cat("Y", "어묵", 10000)])
        p = stock.plan_restock(inv(1250000), xy, [dem("어묵", 10000)], {}, focus_pol("X", per_sku_qty=20), NOW)
        self.assertEqual(p["headroom"], 250000)
        self.assertEqual(p["buy"], [{"sku": "X", "qty": 16}, {"sku": "Y", "qty": 17}])
        self.assertEqual(p["worst_cost"], 247500)
        # the focus share left is too small, the whole headroom is not: still focus_cap
        p = stock.plan_restock(inv(1200000), c, [], {}, focus_pol("G1", focus_cap_share=0.1), NOW)
        self.assertEqual(p["excluded"]["G1"], "focus_cap")
        p = stock.plan_restock(inv(1000000), c, [], {}, focus_pol("G1"), NOW)
        self.assertEqual(p["excluded"]["G1"], "D1_cap")                  # no headroom at all

    def test_focus_still_obeys_d14_and_short_shelf_food(self):
        units = [unit("G2", 200000, "goods", supplied=NOW - 6 * 86400)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, focus_pol("G2", "S1", "nope"), NOW)
        self.assertIn({"sku": "G2", "qty": 1}, p["buy"])                  # a stale unit no longer blocks focus
        self.assertEqual(p["excluded"]["S1"], "D2_short_shelf_wait")
        self.assertEqual(p["excluded"]["nope"], "not_in_catalog")          # an unknown SKU is never bought
        self.assertNotIn("nope", [b["sku"] for b in p["buy"]])
        s = {"buyer": {"category": {"우유": {"uwh": {"n": 10, "median": 20}}}}}
        p = stock.plan_restock(inv(), CATALOG, [], s, focus_pol("S1"), NOW)
        self.assertEqual(p["buy"], [{"sku": "S1", "qty": 2}])
        old = focus_pol("G2", taper_h=72, goods_stop_h=24)
        p = stock.plan_restock(inv(), CATALOG, [], {}, old, END - 20 * 3600)
        self.assertEqual(p["excluded"]["G2"], "D14_goods_stop")
        old = focus_pol("F2", taper_h=72, goods_stop_h=24)
        p = stock.plan_restock(inv(), CATALOG, [], {}, old, END - 30 * 3600)
        self.assertEqual(p["excluded"]["F2"], "D14_taper")                 # no sales: nothing sells before the end


def msum(covered_h=0, rate=None, settles=None, supply=None, p_clear=None, c_kind=None, sold=None, **extra):
    """A summary with memory/ingest.render_market's `market` part, built at NOW (folded covered_h hours)."""
    return dict({"built_at": NOW, "seller": {"skus": sold or {}},
                 "market": {"since": NOW - covered_h * 3600, "sku_rate": rate or {}, "sku_settles_48h": settles or {},
                            "supply_1h": supply or {}, "p_clear": p_clear or {}, "c_kind": c_kind or {}}}, **extra)


def buys(p):
    return {b["sku"]: b["qty"] for b in p["buy"]}


class TrendAndDepthTest(unittest.TestCase):
    """stock.trend_skus, max_hold, the food shelf cap and min_line_qty."""

    def test_trend_skus_take_the_focus_path_after_focus(self):
        pol = focus_pol("F1b", trend_skus=["G2", "F2"], trend_max=4)
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, pol, NOW)
        skus = [b["sku"] for b in p["buy"]]
        self.assertEqual(skus[:3], ["F1b", "F2", "G2"])               # focus, then trend (F2 is its category's pick)
        self.assertEqual(skus[3:], ["F1a", "F3", "G1"])
        self.assertEqual(p["excluded"]["침대"], "D2_goods_over_max_list")    # G2 only through the list
        # a SKU in both lists is a focus SKU
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("F2", trend_skus=["F2"], trend_max=4), NOW)
        self.assertEqual(p["buy"][0], {"sku": "F2", "qty": 2})

    def test_listed_skus_cover_a_share_of_the_market_rate(self):
        s = msum(rate={"F2": 100.0, "F3": 100.0, "F1a": 100.0})
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol("F1a", trend_skus=["F2"], trend_max=4), NOW)
        got = buys(p)
        self.assertEqual(got["F2"], 5)                     # trend: 0.08 x 100 = 8/h x 25/60 x 1.5 = 5
        self.assertEqual(got["F1a"], 10)                   # focus: the same x focus_cover_mult 2
        self.assertEqual(got["F3"], 2)                     # not listed: our own rate (none), per_sku_qty
        s["seller"]["skus"] = {"F2": {"sales_per_h": 16}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol(trend_skus=["F2"], trend_max=4), NOW)
        self.assertEqual(buys(p)["F2"], 10)                # our own rate when it is higher

    def test_food_cover_stays_within_a_quarter_of_shelf_life(self):
        c = dict([cat("T", "두부", 10000, shelf=8)])
        s = {"seller": {"skus": {"T": {"sales_per_h": 6}}}, "buyer": {"category": {"두부": {"uwh": {"n": 10}}}}}
        pol = focus_pol("T", cover_min=120, focus_cover_mult=1.0)
        self.assertEqual(buys(stock.plan_restock(inv(), c, [], s, pol, NOW))["T"], 12)    # 6 x 0.25 x 8, not 18
        s["seller"]["skus"] = {}
        self.assertEqual(buys(stock.plan_restock(inv(), c, [], s, pol, NOW))["T"], 2)     # per_sku_qty still

    def test_small_top_ups_are_skipped(self):
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 8}}}}            # target 8 x 25/60 x 1.5 = 5
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)] * 3), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(p["excluded"]["F1a"], "small_topup")           # 2 < min(4, 2.5)
        self.assertEqual(buys(p)["F3"], 2)                              # sold out: per_sku_qty as before
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(buys(p)["F1a"], 4)
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, {}, POL, NOW)
        self.assertEqual(buys(p)["F1a"], 1)                             # 1 of target 2: min(4, 1) = 1 is enough
        pol = copy.deepcopy(POL)
        pol["stock"]["min_line_qty"] = 1
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)] * 3), CATALOG, DEMAND, s, pol, NOW)
        self.assertEqual(buys(p)["F1a"], 2)


class BigTicketTest(unittest.TestCase):
    """Goods listed above goods_max_list: the budget and the probe (G2, list 300,000, worst 225,000 a unit)."""

    RICH = 50000000                                                     # headroom 15M, focus room 7.5M

    @staticmethod
    def dear(n, cost=290000):                                           # 290,000 x 1.08 > 300,000: overpriced
        return [dict(unit("G2", cost, "goods"), unit=f"u{i}", list_price=300000) for i in range(n)]

    def test_probe_holds_a_few_units_until_two_sales(self):
        pol = focus_pol("G2", "F2", per_sku_qty=5)
        p = stock.plan_restock(inv(self.RICH), CATALOG, [], {}, pol, NOW)
        self.assertEqual(buys(p), {"G2": 3, "F2": 5})                  # bigticket_probe_qty 3; F2 is not big
        p = stock.plan_restock(inv(self.RICH, self.dear(1)), CATALOG, [], {}, pol, NOW)
        self.assertEqual(buys(p)["G2"], 2)                             # overpriced big-ticket units count
        self.assertEqual(p["overpriced"], 1)
        p = stock.plan_restock(inv(self.RICH, self.dear(2)), CATALOG, [], {}, pol, NOW)
        self.assertEqual(p["excluded"]["G2"], "small_topup")          # 1 < min(4, 3 / 2)
        p = stock.plan_restock(inv(self.RICH, self.dear(3)), CATALOG, [], {}, pol, NOW)
        self.assertEqual(p["excluded"]["G2"], "big_ticket_probe")
        sold = {"seller": {"skus": {"G2": {"sales_per_h": 0.1, "sold_6h": 2}}}}
        p = stock.plan_restock(inv(self.RICH, self.dear(1)), CATALOG, [], sold, pol, NOW)
        self.assertEqual(buys(p)["G2"], 4)                             # two sales in 6 h: per_sku_qty 5 again
        p = stock.plan_restock(inv(self.RICH, self.dear(5)), CATALOG, [], {}, pol, NOW)
        self.assertEqual(p["excluded"]["G2"], "stocked")

    def test_budget_counts_held_cost_and_planned_worst_cost(self):
        pol = focus_pol("G2", big_ticket_share=0.05)                   # 0.05 x 0.3 x 50M = 750,000
        p = stock.plan_restock(inv(self.RICH), CATALOG, [], {}, pol, NOW)
        self.assertEqual(buys(p), {"G2": 2})
        self.assertEqual(p["big_ticket"], {"budget": 750000, "held_cost": 0, "planned_worst": 450000})
        pol = focus_pol("G2", trend_skus=["G1"], big_ticket_share=0.03)   # 0.03 x 0.3 x 50.29M = 452,610
        p = stock.plan_restock(inv(self.RICH, self.dear(1)), CATALOG, [], {}, pol, NOW)
        self.assertEqual(p["excluded"]["G2"], "big_ticket_cap")       # 290,000 held + 225,000 > 452,610
        self.assertEqual(p["big_ticket"]["held_cost"], 290000)
        self.assertEqual(buys(p), {"G1": 2})                          # G1 (40,000) is not big-ticket


class SurgeAllowanceTest(unittest.TestCase):
    """Patch 3.2: 50 free units a SKU in the market's rolling hour, then +0.2 % a unit; f_max per kind."""

    PRICES = {"p_clear": {"food": 0.707, "goods": 0.74}, "c_kind": {"food": 0.51, "goods": 0.61}}

    def test_f_max_from_the_clearing_price(self):
        s = msum(**self.PRICES)
        self.assertAlmostEqual(stock.f_max(s, "food", POL), 0.657 / (0.51 * 1.08))     # 1.193
        self.assertAlmostEqual(stock.f_max(s, "goods", POL), 0.69 / (0.61 * 1.08))     # 1.047
        self.assertAlmostEqual(stock.f_max({}, "goods", POL), 1 / (0.75 * 1.08))       # no p_clear: the old cut
        self.assertEqual(stock.f_max(msum(p_clear={"food": 0.99}), "food", POL), 1.25)  # never above F_MAX_CAP
        self.assertEqual(stock.f_max(msum(p_clear={"goods": 0.5}), "goods", POL), 1.0)

    def test_line_stops_where_the_factor_passes_f_max(self):
        sold = {"F1a": {"sales_per_h": 12}}                             # target 8
        s = msum(supply={"F1a": 140}, sold=sold, **self.PRICES)         # factor 1.18 <= food f_max 1.193
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(buys(p)["F1a"], 6)                             # 50 + 96.4 - 140 units left under f_max
        self.assertEqual(p["surge"]["F1a"], {"factor": 1.18, "u1h": 140, "free_left": 0, "f_max": 1.1928})
        # worst cost at the line's mean factor 1 + 0.002 x (140 + 3 - 50) = 1.186
        rest = sum(q * 0.75 * CATALOG[k]["list_price"] for k, q in buys(p).items() if k != "F1a")
        self.assertEqual(p["worst_cost"], int(round(rest + 6 * 0.75 * 10000 * 1.186)))
        s = msum(supply={"F1a": 40}, sold=sold, **self.PRICES)
        self.assertEqual(buys(stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW))["F1a"], 8)

    def test_factor_above_f_max_skips_and_our_planned_units_count(self):
        s = msum(**self.PRICES)
        p = stock.plan_restock(dict(inv(), surge=[{"sku": "G1", "units": 80, "factor": 1.06}]), CATALOG, DEMAND, s,
                               POL, NOW)
        self.assertEqual(p["excluded"]["G1"], "surge")                  # 1.06 > goods f_max 1.047
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW, planned={"G1": 72})
        self.assertEqual((buys(p)["G1"], p["surge"]["G1"]["u1h"]), (1, 72))   # floor(50 + 23.7 - 72) = 1
        s = msum(supply={"G1": 30}, **self.PRICES)
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW, planned={"G1": 44})
        self.assertEqual(p["excluded"]["G1"], "surge")                  # fold 30 + ours 44 = 74: 1.048 > 1.047
        self.assertEqual(p["surge"]["G1"]["u1h"], 74)

    def test_planned_units_add_to_the_fold_not_to_the_live_count(self):
        # inventory.json's surge units are the market's live count, our bought lines included
        s = msum(supply={"F1a": 20}, **self.PRICES)
        live = dict(inv(), surge=[{"sku": "F1a", "units": 80, "factor": 1.06}])
        p = stock.plan_restock(live, CATALOG, DEMAND, s, POL, NOW, planned={"F1a": 40})
        self.assertEqual(p["surge"]["F1a"]["u1h"], 80)                  # max(80, 20 + 40), not 80 + 40
        p = stock.plan_restock(live, CATALOG, DEMAND, s, POL, NOW, planned={"F1a": 70})
        self.assertEqual(p["surge"]["F1a"]["u1h"], 90)                  # our unseen lines outrun the live row

    def test_a_surge_cut_line_below_min_line_qty_is_skipped_when_in_stock(self):
        sold = {"F1a": {"sales_per_h": 24}}                             # target 15
        s = msum(supply={"F1a": 144}, sold=sold, **self.PRICES)         # floor(50 + 96.4 - 144) = 2 left
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(p["excluded"]["F1a"], "surge")                 # 2 < min(4, 7.5): not worth a line
        self.assertEqual(buys(stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW))["F1a"], 2)   # sold out
        pol = copy.deepcopy(POL)
        pol["stock"]["max_qty"] = 3                                     # a small line cap alone is not a surge cut
        s = msum(sold=sold, **self.PRICES)
        self.assertEqual(buys(stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, pol, NOW))["F1a"], 3)

    def test_no_market_data_is_the_free_units_and_the_old_cut(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, POL, NOW)
        self.assertEqual(p["surge"]["F1a"], {"factor": 1.0, "u1h": 0, "free_left": 50, "f_max": 1.2346})
        self.assertEqual(stock.surge_units({"surge": [{"sku": "A", "units": 7}, {"sku": "B", "units": "x"},
                                                      {"sku": "C", "units": -1}, "junk"]}), {"A": 7})


class StaleAndLongTailTest(unittest.TestCase):
    STALE = [unit("G1", 20000, "goods", supplied=NOW - 6 * 86400)]

    def test_stale_goods_block_only_unsold_unlisted_skus(self):
        p = stock.plan_restock(inv(units=self.STALE), CATALOG, DEMAND, msum(settles={"G1": 3}), POL, NOW)
        self.assertEqual(buys(p)["G1"], 1)                              # the market still sells it
        for s in ({}, msum()):                                          # no data, or no sale in 48 h
            p = stock.plan_restock(inv(units=self.STALE), CATALOG, DEMAND, s, POL, NOW)
            self.assertEqual(p["excluded"]["G1"], "D4_stale_goods")
        p = stock.plan_restock(inv(units=self.STALE), CATALOG, DEMAND, msum(), focus_pol(trend_skus=["G1"]), NOW)
        self.assertEqual(buys(p)["G1"], 1)                              # a trend SKU is not blocked

    def test_long_tail_picks_without_market_sales_are_not_bought(self):
        s = msum(covered_h=30, settles={"F1a": 5})
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(list(buys(p)), ["F1a"])
        self.assertEqual({k: p["excluded"][k] for k in ("F3", "F2", "G1")},
                         {"F3": "no_market_sales", "F2": "no_market_sales", "G1": "no_market_sales"})
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol("F3"), NOW)
        self.assertEqual(list(buys(p)), ["F3", "F1a"])                  # a focus pick is kept
        p = stock.plan_restock(inv(), CATALOG, DEMAND, msum(covered_h=10, settles={"F1a": 5}), POL, NOW)
        self.assertEqual(list(buys(p)), ["F1a", "F3", "F2", "G1"])      # 10 h folded is not evidence yet


class WindowTest(unittest.TestCase):
    def test_window_reading(self):
        w = stock.supply_window({"restock": {"open": False, "opens_at": NOW + 600, "closes_at": NOW + 780}}, NOW)
        self.assertEqual(w, {"open": False, "opens_at": NOW + 600, "closes_at": NOW + 780, "in_s": 600,
                             "closes_in_s": 780})
        self.assertTrue(stock.window_closed(w, POL))
        w = stock.supply_window({"restock": {"open": False, "opens_at": NOW + 60, "closes_at": NOW + 240}}, NOW)
        self.assertFalse(stock.window_closed(w, POL))                # opens within window_lead_s
        w = stock.supply_window({"restock": {"open": True, "opens_at": NOW - 30, "closes_at": NOW + 150}}, NOW)
        self.assertFalse(stock.window_closed(w, POL))
        w = stock.supply_window(inv(), NOW)                          # an older market: read as open
        self.assertEqual(w, {"open": None, "opens_at": None, "closes_at": None, "in_s": None, "closes_in_s": None})
        self.assertFalse(stock.window_closed(w, POL))
        self.assertFalse(stock.window_closed(stock.supply_window({"restock": {"open": False}}, NOW), POL))
        for bad in (float("nan"), float("inf"), "soon", True):       # read as unknown, never raise
            w = stock.supply_window({"restock": {"open": False, "opens_at": bad, "closes_at": bad}}, NOW)
            self.assertEqual((w["in_s"], w["closes_in_s"]), (None, None))

    def test_lines_that_fit_before_the_close(self):
        fit = lambda r: stock.lines_that_fit(stock.supply_window({"restock": r}, NOW))
        self.assertEqual(fit({"open": True, "closes_at": NOW + 150}), 14)     # (150 - 2) / 11 s a call
        self.assertEqual(fit({"open": True, "closes_at": NOW + 5}), 1)       # the first call goes out at once
        self.assertEqual(fit({"open": True, "closes_at": NOW + 13}), 1)
        self.assertEqual(fit({"open": True, "closes_at": NOW + 24}), 2)
        self.assertEqual(fit({"open": True, "closes_at": NOW + 2}), 0)       # inside the close margin
        self.assertEqual(fit({"open": True, "closes_at": NOW - 1}), 0)
        self.assertIsNone(fit({"open": False, "closes_at": NOW + 150}))
        self.assertIsNone(fit({"open": True}))

    def test_held(self):
        units = [unit("F1a", 6000), unit("F1a", 6000), unit("F2", 15000), unit("F3", 3000, grade="expired")]
        self.assertEqual(stock.held(inv(units=units)), {"skus": 2, "units": 3})
        self.assertEqual(stock.held(None), {"skus": 0, "units": 0})


class BankruptTest(unittest.TestCase):
    QUIET = {"started_at": NOW - 13 * 3600, "seller": {"sold_24h": 0, "last_sale_ts": None}}

    def test_files_only_under_all_d10_conditions(self):
        low = inv(400000, [unit("F1a", 600000)])
        self.assertTrue(stock.should_bankrupt(low, self.QUIET, POL, NOW)[0])
        self.assertFalse(stock.should_bankrupt(inv(600000), self.QUIET, POL, NOW)[0])           # above the line
        none_left = dict(low, bankruptcies_left=0)
        self.assertFalse(stock.should_bankrupt(none_left, self.QUIET, POL, NOW)[0])
        self.assertFalse(stock.should_bankrupt(low, self.QUIET, POL, END - 47 * 3600)[0])     # < 48 h to the end
        self.assertFalse(stock.should_bankrupt(low, {}, POL, NOW)[0])                         # no memory
        selling = {"started_at": NOW - 30 * 3600, "seller": {"sold_24h": 10, "last_sale_ts": NOW - 3600}}
        rich_stock = inv(400000, [unit("G1", 400000, "goods") for _ in range(5)])
        ok, why = stock.should_bankrupt(rich_stock, selling, POL, NOW)
        self.assertFalse(ok)                                       # 2,000,000 x 10/15 recovers enough
        self.assertEqual(why["recover"], 1333333)
        slow = {"started_at": NOW - 30 * 3600, "seller": {"sold_24h": 1, "last_sale_ts": NOW - 13 * 3600}}
        self.assertTrue(stock.should_bankrupt(rich_stock, slow, POL, NOW)[0])               # no sale for 12 h


class OwnCardTest(unittest.TestCase):
    def test_card_check(self):
        ok = mock.Mock(status_code=200, json=lambda: {"name": "psyoung82 shop"})
        with mock.patch.object(stock.httpx, "get", return_value=ok) as g:
            self.assertTrue(stock.own_card_ok("https://psy.example/"))
            self.assertEqual(g.call_args.args[0], "https://psy.example/.well-known/agent-card.json")
            self.assertEqual(g.call_args.kwargs["timeout"], 3.0)
        with mock.patch.object(stock.httpx, "get", return_value=mock.Mock(status_code=502)):
            self.assertFalse(stock.own_card_ok("https://psy.example"))
        with mock.patch.object(stock.httpx, "get", side_effect=stock.httpx.ConnectError("down")):
            self.assertFalse(stock.own_card_ok("https://psy.example"))
        with mock.patch.object(stock.httpx, "get", side_effect=AssertionError("must not call")):
            self.assertTrue(stock.own_card_ok(""))


class _Ledger(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"[]"
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class HookTest(unittest.TestCase):
    """restock.py as the kit runs it: cwd is the agent folder, the last stdout line is read."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ledger)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", self.home / "strategy" / "SKILL.md")
        self.state = self.home / ".market"
        self.state.mkdir()
        (self.state / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        (self.state / "demand.json").write_text(json.dumps(DEMAND, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("OWN_SHOP_URL",)}
        env.update(R3_HOME=str(self.home), MARKET_URL=f"http://127.0.0.1:{self.srv.server_address[1]}")
        t = time.time()
        p = subprocess.run([sys.executable, "-B", "restock.py", *args], cwd=W, env=env,
                           capture_output=True, text=True, timeout=40)
        self.assertLess(time.time() - t, 30)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        self.assertEqual(p.stderr, "")
        return json.loads(lines[0])

    def write_inv(self, obj):
        (self.state / "inventory.json").write_text(json.dumps(obj), encoding="utf-8")

    def test_plans_and_logs(self):
        self.write_inv(inv())
        out = self.run_hook("--state", str(self.state))
        self.assertEqual(set(out), {"buy"})
        self.assertTrue(out["buy"])
        rows = [json.loads(x) for x in
                (self.home / "logs" / "decisions" / "restock.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["kind"], "restock")
        self.assertEqual(rows[-1]["choice"], out["buy"])
        self.assertTrue(rows[-1]["memory_refs"])
        self.assertEqual(rows[-1]["inputs"]["window"], {"open": None, "opens_at": None, "closes_at": None,
                                                        "in_s": None, "closes_in_s": None})
        self.assertEqual(rows[-1]["inputs"]["stock"], {"skus": 0, "units": 0, "empty_picks": 4, "overpriced": 0})
        self.assertTrue((self.home / "memory" / "summary.json").exists())
        self.assertTrue((self.home / "memory" / "cursor.json").exists())

    def rows(self):
        return [json.loads(x) for x in
                (self.home / "logs" / "decisions" / "restock.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_closed_window_buys_nothing_but_ingests(self):
        now = time.time()
        self.write_inv(dict(inv(), restock={"open": False, "opens_at": now + 600, "closes_at": now + 780}))
        self.assertEqual(self.run_hook("--state", str(self.state)), {"buy": []})
        row = self.rows()[-1]
        self.assertEqual((row["kind"], row["choice"]), ("restock", []))
        self.assertTrue(row["why"].startswith("supply window closed; opens in "))
        self.assertFalse(row["inputs"]["window"]["open"])
        self.assertTrue(590 <= row["inputs"]["window"]["in_s"] <= 600)
        self.assertEqual(row["inputs"]["stock"], {"skus": 0, "units": 0, "empty_picks": 4, "overpriced": 0})
        self.assertTrue((self.home / "memory" / "summary.json").exists())

    def test_plans_when_open_or_opening_soon(self):
        now = time.time()
        for r in ({"open": False, "opens_at": now + 30, "closes_at": now + 210},
                  {"open": True, "opens_at": now - 30, "closes_at": now + 150}):
            self.write_inv(dict(inv(), restock=r))
            self.assertTrue(self.run_hook("--state", str(self.state))["buy"])
            self.assertEqual(self.rows()[-1]["inputs"]["window"]["open"], r["open"])

    def test_open_or_opening_window_skips_ingest(self):
        now = time.time()
        for r in ({"open": True, "opens_at": now - 30, "closes_at": now + 150},
                  {"open": False, "opens_at": now + 30, "closes_at": now + 210}):
            self.write_inv(dict(inv(), restock=r))
            self.assertTrue(self.run_hook("--state", str(self.state))["buy"])
            self.assertEqual(self.rows()[-1]["inputs"]["ingest"], {"skipped": "window"})
            self.assertFalse((self.home / "memory" / "cursor.json").exists())

    def test_closed_or_unknown_window_ingests(self):
        now = time.time()
        for r in ({"open": False, "opens_at": now + 600, "closes_at": now + 780},
                  {"open": False},                          # a market patch dropped opens_at: never skip for good
                  None):                                    # no restock key at all
            self.write_inv(dict(inv(), **({"restock": r} if r is not None else {})))
            self.run_hook("--state", str(self.state))
            self.assertIn("new_events", self.rows()[-1]["inputs"]["ingest"], r)
            self.assertTrue((self.home / "memory" / "cursor.json").exists())

    def test_closing_window_buys_only_what_fits(self):
        now = time.time()
        # 20 s: two lines while closes_in_s is 14 to 24 s when the hook reads it
        self.write_inv(dict(inv(), restock={"open": True, "opens_at": now - 170, "closes_at": now + 20}))
        out = self.run_hook("--state", str(self.state))
        self.assertEqual(len(out["buy"]), 2)
        self.assertEqual(self.rows()[-1]["inputs"]["excluded"].get("window_closing"), 2)

    def test_focus_inputs_in_the_decision(self):
        self.write_inv(inv())
        self.run_hook("--state", str(self.state))
        self.assertEqual(self.rows()[-1]["inputs"]["focus"], {"skus": 0, "planned": 0, "excluded": {}})
        self.assertEqual(self.rows()[-1]["inputs"]["trend"], {"skus": 0, "planned": 0, "excluded": {}})
        md = self.home / "strategy" / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8").replace(
            "focus_skus: []", 'focus_skus: ["G2", "S1", "nope"]', 1), encoding="utf-8")
        out = self.run_hook("--state", str(self.state))
        self.assertEqual(out["buy"][0]["sku"], "G2")
        self.assertEqual(self.rows()[-1]["inputs"]["focus"],
                         {"skus": 3, "planned": 1, "excluded": {"S1": "D2_short_shelf_wait", "nope": "not_in_catalog"}})

    def test_surge_and_overpriced_inputs_in_the_decision(self):
        dear = dict(unit("G1", 39000, "goods"), list_price=40000)
        self.write_inv(dict(inv(units=[dear]), surge=[{"sku": "G1", "units": 60, "factor": 1.02},
                                                       {"sku": "G2", "units": 60, "factor": 1.02}]))
        out = self.run_hook("--state", str(self.state))
        row = self.rows()[-1]
        g1 = next(b["qty"] for b in out["buy"] if b["sku"] == "G1")
        # G2 is neither planned nor focus; goods f_max without p_clear: 1 / (0.75 x 1.08) -> 1.2346
        self.assertEqual(row["inputs"]["surge"], {"G1": {"factor": 1.02, "u1h": 60, "free_left": 0, "f_max": 1.2346}})
        self.assertEqual(row["inputs"]["stock"]["overpriced"], 1)
        # 0.4 x 0.3 x (5,000,000 + 39,000)
        self.assertEqual(row["inputs"]["big_ticket"], {"budget": 604680, "held_cost": 0, "planned_worst": 0})
        # the planned line is kept for the rolling hour and counts in the next plan's u1h
        saved = json.loads((self.home / "memory" / "restock_planned.json").read_text(encoding="utf-8"))
        self.assertIn(["G1", g1], [r[1:] for r in saved["lines"]])
        self.run_hook("--state", str(self.state))                       # the live count already holds our line
        self.assertEqual(self.rows()[-1]["inputs"]["surge"]["G1"]["u1h"], 60)
        saved = json.loads((self.home / "memory" / "restock_planned.json").read_text(encoding="utf-8"))
        ours = sum(r[2] for r in saved["lines"] if r[1] == "G1")
        self.write_inv(inv(units=[dear]))                                # under the free units: no live row
        self.run_hook("--state", str(self.state))
        self.assertEqual(self.rows()[-1]["inputs"]["surge"]["G1"]["u1h"], ours)

    def test_planned_units_left_out_once_the_fold_has_them(self):
        import restock
        path = self.home / "planned.json"
        path.write_text(json.dumps({"lines": [[NOW - 4000, "A", 9], [NOW - 1800, "A", 5], [NOW - 300, "A", 3],
                                              [NOW - 60, "B", True], ["x", "B", 2], [NOW - 10, "B", 4]]}),
                        encoding="utf-8")
        self.assertEqual(restock.planned_units(path, NOW), {"A": 8, "B": 4})        # the last hour, bad rows out
        self.assertEqual(restock.planned_units(path, NOW, since=NOW - 900), {"A": 3, "B": 4})

    def test_bankrupt_line(self):
        self.write_inv(inv(300000))
        self.assertEqual(self.run_hook("--state", str(self.state)), {"buy": [], "bankrupt": True})

    def test_one_line_on_errors(self):
        self.write_inv([1, 2])
        self.assertEqual(self.run_hook("--state", str(self.state)), {"buy": []})
        self.assertEqual(self.run_hook("--bogus"), {"buy": []})
        self.assertIn("failed, buying nothing",
                      (self.home / "logs" / "hooks" / "restock.log").read_text(encoding="utf-8"))

    def test_hard_limit_is_not_swallowed_by_the_ingest_guard(self):
        import restock
        from memory import ingest
        self.write_inv(inv())
        with mock.patch.object(ingest, "sync", side_effect=restock._Late("late")), \
                mock.patch.object(stock, "plan_restock", side_effect=AssertionError("must not plan")):
            with self.assertRaises(restock._Late):
                restock.run(self.state, time.time())

    def test_invalid_policy_still_plans_and_records_it(self):
        (self.home / "strategy" / "SKILL.md").write_text("no front matter", encoding="utf-8")
        self.write_inv(inv())
        self.assertTrue(self.run_hook("--state", str(self.state))["buy"])
        kinds = [json.loads(x)["kind"] for x in
                 (self.home / "logs" / "decisions" / "restock.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["policy_error", "restock"])

    def test_missing_state_files(self):
        self.assertEqual(self.run_hook("--state", str(self.home / "nowhere")), {"buy": []})


if __name__ == "__main__":
    unittest.main()
