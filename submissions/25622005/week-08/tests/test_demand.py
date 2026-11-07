"""memory/demand.py: the shelf tick, the hour profile factor, the in-stock rate, the share and forecast()."""
import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-demand-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import policy  # noqa: E402
from memory import demand  # noqa: E402
from tools import stock  # noqa: E402

NOW = 1_794_013_200.0                      # 2026-11-07 10:00 KST on this machine's clock (hour key H)
H = int(NOW // 3600)
POL = copy.deepcopy(policy.DEFAULTS)


def hours(back, f):
    return {str(H - i): f(i) for i in range(back)}


def summary(own=None, mkt=None, rate=None, profile=None):
    return {"market": {"own_sku_hour": {"G1": own or {}}, "sku_hour_24": {"G1": mkt or {}},
                       "sku_rate": {"G1": rate} if rate is not None else {}, "hour_profile": profile or {}}}


def instock(mins_by_offset):
    return {"v": 1, "last": NOW, "hours": {str(H - i): {"G1": m} for i, m in mins_by_offset.items()}}


class TickTest(unittest.TestCase):
    def test_tick_adds_minutes_to_live_skus_and_prunes(self):
        inv = {"units": [{"sku": "G1", "grade": "A"}, {"sku": "G1", "grade": "B"}, {"sku": "G2", "grade": "expired"}, {"sku": "F", "grade": "C"}]}
        st = demand.tick_instock(None, inv, NOW)
        self.assertEqual(st["hours"][str(H)], {"G1": 1.0, "F": 1.0})          # the first tick counts a minute
        st = demand.tick_instock(st, inv, NOW + 90)
        self.assertEqual(st["hours"][str(H)]["G1"], 2.5)
        st = demand.tick_instock(st, inv, NOW + 90 + 3600)                     # a long gap counts TICK_MAX_S
        self.assertEqual(st["hours"][str(H + 1)]["G1"], 5.0)
        st["hours"][str(H - 60)] = {"G1": 10}
        st = demand.tick_instock(st, {"units": []}, NOW + 200 + 3600)
        self.assertNotIn(str(H - 60), st["hours"])
        self.assertEqual(st["last"], NOW + 200 + 3600)
        self.assertEqual(demand.tick_instock({"hours": "junk"}, inv, NOW)["hours"][str(H)]["F"], 1.0)


class FactorTest(unittest.TestCase):
    def test_hour_factor_scales_by_the_profile_of_the_coming_hours(self):
        profile = {f"{h:02d}": (600.0 if 3 <= h <= 9 else 270.0) for h in range(24)}
        s = summary(profile=profile)
        mean = sum(profile.values()) / 24
        hh = int(time.strftime("%H", time.localtime(NOW)))
        want = sum(profile[f"{(hh + i) % 24:02d}"] for i in range(2)) / 2 / mean          # a 1.5 h window spans 2 hours
        self.assertEqual(demand.hour_factor(s, NOW, 1.5), round(min(2.0, max(0.5, want)), 3))
        self.assertIsNone(demand.hour_factor(summary(profile={"03": 1}), NOW, 1.5))      # too few hours
        flat = {f"{h:02d}": 100.0 for h in range(24)}
        self.assertEqual(demand.hour_factor(summary(profile=flat), NOW, 1.5), 1.0)


class ForecastTest(unittest.TestCase):
    def test_instock_rate_counts_only_shelf_hours(self):
        # sold 9 in the 3 hours on the shelf (offsets 1-3); off the shelf for the other 21 hours
        s = summary(own=hours(4, lambda i: 3 if i else 0))
        self.assertEqual(demand.instock_stats(s, instock({1: 60, 2: 60, 3: 60}), "G1", NOW), (9, 3.0, 0))
        f = demand.forecast(s, instock({1: 60, 2: 60, 3: 60}), "G1", NOW, 1.5, POL)
        self.assertEqual((f["rate"], f["src"], f["instock_h"], f["sold"], f["factor"]), (3.0, "instock", 3.0, 9, None))

    def test_share_times_market_rate_when_the_market_moves_faster(self):
        s = summary(own=hours(3, lambda i: 2 if i else 0), mkt=hours(3, lambda i: 20 if i else 0), rate=60.0)
        f = demand.forecast(s, instock({1: 60, 2: 30}), "G1", NOW, 1.5, POL)    # 4 sold in 1.5 h = 2.67/h; share 4/40 x 60 = 6/h
        self.assertEqual((f["share"], f["rate"], f["src"]), (0.1, 6.0, "share"))
        pol = copy.deepcopy(POL)
        pol["stock"]["demand_share_max"] = 0.05
        self.assertEqual(demand.forecast(s, instock({1: 60, 2: 30}), "G1", NOW, 1.5, pol)["rate"], 3.0)
        # under MIN_INSTOCK_MIN minutes an hour does not count in the share, and under MIN_MARKET_N no share
        f = demand.forecast(s, instock({1: 10, 2: 10}), "G1", NOW, 1.5, POL)
        self.assertEqual((f["share"], f["src"]), (None, None))

    def test_no_data_means_no_rate(self):
        f = demand.forecast({}, None, "G1", NOW, 1.5, POL)
        self.assertEqual((f["rate"], f["src"], f["instock_h"]), (None, None, 0.0))

    def test_the_hour_factor_scales_the_rate(self):
        flat = {f"{h:02d}": 100.0 for h in range(24)}
        hh = time.strftime("%H", time.localtime(NOW))
        flat[hh] = 200.0                                                         # this hour is twice the mean-ish
        s = summary(own=hours(4, lambda i: 3 if i else 0), profile=flat)
        f = demand.forecast(s, instock({1: 60, 2: 60, 3: 60}), "G1", NOW, 1.0, POL)
        self.assertEqual(f["factor"], round(200 / (2500 / 24), 3))
        self.assertEqual(f["rate"], round(3.0 * f["factor"], 4))


class PlannerTest(unittest.TestCase):
    """The forecast lifts the planner's rate; stock.demand_model off keeps the plain rate."""
    def test_forecast_raises_the_cover_target(self):
        from tests.test_restock import CATALOG, DEMAND, POL as RPOL, inv, NOW as RNOW
        h = int(RNOW // 3600)
        s = {"seller": {"skus": {"F1a": {"settled": 2, "sales_per_h": 0.1, "sold_6h": 0}}},
             "market": {"own_sku_hour": {"F1a": {str(h - 1): 6, str(h - 2): 6}}, "sku_hour_24": {}, "sku_rate": {}, "hour_profile": {}}}
        ist = {"v": 1, "last": RNOW, "hours": {str(h - 1): {"F1a": 60}, str(h - 2): {"F1a": 60}}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, RPOL, RNOW, instock=ist)
        line = next(b for b in p["buy"] if b["sku"] == "F1a")
        self.assertEqual(p["demand"]["F1a"]["rate"], 6.0)                        # 12 sold in 2 shelf hours
        want = __import__("math").ceil(6.0 * RPOL["stock"]["cover_min"] / 60 * RPOL["stock"]["cover_safety"])
        self.assertEqual(line["qty"], min(want, RPOL["stock"]["max_qty"]))
        off = copy.deepcopy(RPOL)
        off["stock"]["demand_model"] = False
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, off, RNOW, instock=ist)
        self.assertEqual(p["demand"], {})
        self.assertEqual(next(b for b in p["buy"] if b["sku"] == "F1a")["qty"], RPOL["stock"]["per_sku_qty"])


if __name__ == "__main__":
    unittest.main()
