"""The kit buys only the first 10 lines of a plan (scripts/market_kit.py: `for b in buy[:10]`). Since 11-10 every restock pass stops
opening lines at 10, in the same order as before (food trial, food probe, shop b's band, 1M+ probe, ordinary), so what is planned and
budgeted is what the kit can buy. Lines already open still deepen."""

import unittest
from unittest.mock import patch

import restock_policy as rp

RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 80, "max_proven_units_per_sku": 3, "max_units_per_run": 12, "max_lines_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}
NOW = 1793800000.0
CHEAP = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 95}
HI_LOW = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 700000}  # under the .75-of-list worst cost


def catalog(band_skus=8):
    out = {f"c{i}": {"category": "cat", "kind": "goods", "list_price": 100} for i in range(10)}
    for i in range(band_skus):
        out[f"H{i}"] = {"category": "cat", "kind": "goods", "list_price": 1000000}
    for k in ("P1", "P2", "P3"):
        out[k] = {"category": "cat", "kind": "goods", "list_price": 1000000}
    return out


def inventory(balance=10000000000):
    return {"round": 3, "balance": balance, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": [], "surge": []}


class KitLines(unittest.TestCase):
    def plan(self, band_skus=8, probe=None, slots=20):
        cat = catalog(band_skus)
        intel = {k: (CHEAP if k.startswith("c") else HI_LOW) for k in cat}
        band = {"slots": slots, "min_list_price": 1000000, "sales": {f"H{i}": 2 for i in range(band_skus)}, "max_worst_cost_total": 24000000}
        with patch.object(rp, "capacity_config", return_value=LIMITS):
            return rp.plan(inventory(), cat, [{"category": "cat"}], {}, 0, intelligence=intel, runtime=RUNTIME, now=NOW, hi_band=band, hi_probe=probe)

    def test_never_more_than_ten_lines(self):
        for band_skus, probe in ((8, None), (8, {"skus": ["P1", "P2", "P3"], "max_units": 3}), (2, {"skus": ["P1", "P2", "P3"], "max_units": 3}), (0, None)):
            with self.subTest(band_skus=band_skus, probe=probe):
                buys, why = self.plan(band_skus, probe)
                self.assertLessEqual(len(buys), rp.KIT_LINES)
                self.assertEqual(why["kit_lines"]["total"], len(buys))

    def test_band_keeps_its_priority_and_ordinary_takes_the_rest(self):
        buys, why = self.plan(8, {"skus": ["P1", "P2", "P3"], "max_units": 3})
        lines = why["kit_lines"]
        self.assertEqual((lines["band"], lines["probe"], lines["ordinary"]), (7, 3, 0))  # 20 places over 3, 3, 3, 3, 3, 3, 2
        self.assertEqual(sum(b["qty"] for b in buys if b["sku"].startswith("H")), 20)

    def test_with_few_band_lines_ordinary_keeps_its_six(self):
        buys, why = self.plan(2, {"skus": ["P1"], "max_units": 3})
        lines = why["kit_lines"]
        self.assertEqual((lines["band"], lines["probe"], lines["ordinary"]), (2, 1, 6))
        self.assertEqual(len(buys), 9)

    def test_ordinary_lines_past_the_tenth_are_not_budgeted(self):
        buys, why = self.plan(8, {"skus": ["P1", "P2", "P3"], "max_units": 3})
        planned_cost = sum(b["qty"] * (1000000 if not b["sku"].startswith("c") else 100) * .75 for b in buys)
        self.assertAlmostEqual(why["estimated_max_cost"], planned_cost)


if __name__ == "__main__":
    unittest.main()
