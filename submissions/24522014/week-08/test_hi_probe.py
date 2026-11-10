"""1M+ probe (hi_probe.json), approved by the user on 11-10: at most 3 1M+ goods SKUs we have never sold, 1 unit each, at most 3
probe units held plus planned; places next to shop b's, outside the total cap, inside the band's worst-cost cap and the run budget;
the median test is skipped unless surged; a SKU leaves the probe at its first completed sale of ours. Off, the plan is today's."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import hi_probe
import median_bypass as mb
import restock_policy as rp

RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 10, "max_proven_units_per_sku": 3, "max_units_per_run": 4, "max_lines_per_run": 2, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}
NOW = 1793800000.0
HI_LIST = 2000000
CHEAP = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 95}
# Public medians below our .75-of-list worst cost: the ordinary pass skips these SKUs.
HI_LOW = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 1370000}
BAND = {"slots": 3, "min_list_price": 1000000, "sales": {"H": 2}, "max_worst_cost_total": 24000000}
PROBE = {"skus": ["P1", "P2", "P3"], "max_units": 3}


def catalog():
    out = {f"c{i}": {"category": "cat", "kind": "goods", "list_price": 100} for i in range(6)}
    for k in ("H", "P1", "P2", "P3", "P4"):
        out[k] = {"category": "cat", "kind": "goods", "list_price": HI_LIST}
    return out


def unit(sku, cost=75):
    return {"sku": sku, "cost": cost, "grade": "A", "kind": "goods", "category": "cat", "supplied_at": NOW}


def inventory(units=(), surge=None, balance=1000000000):
    return {"round": 3, "balance": balance, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": list(units), "surge": surge or []}


class Plan(unittest.TestCase):
    def plan(self, inv, band=BAND, probe=PROBE, limits=LIMITS, intel=None):
        cat = catalog()
        intelligence = intel or {**{k: CHEAP for k in cat if k.startswith("c")}, **{k: HI_LOW for k in ("H", "P1", "P2", "P3", "P4")}}
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan(inv, cat, [{"category": "cat"}], {}, 0, intelligence=intelligence, runtime=RUNTIME, now=NOW, hi_band=band, hi_probe=probe)

    def qty(self, buys):
        return {b["sku"]: b["qty"] for b in buys}

    def test_off_is_exactly_today(self):
        for inv in (inventory(), inventory([unit("c0")] * 3 + [unit("H", 1500000)])):
            today = self.plan(inv, probe=None)
            for probe in (None, {"skus": [], "max_units": 3}):
                with self.subTest(probe=probe):
                    self.assertEqual(self.plan(inv, probe=probe), today)

    def test_without_a_valid_band_the_probe_buys_nothing(self):
        buys, _ = self.plan(inventory(), band=None)
        self.assertFalse({"P1", "P2", "P3"} & set(self.qty(buys)))

    def test_one_unit_of_each_probe_sku_past_the_median_test(self):
        buys, why = self.plan(inventory())
        q = self.qty(buys)
        self.assertEqual([q.get(k) for k in ("P1", "P2", "P3")], [1, 1, 1])
        self.assertNotIn("P4", q)
        self.assertEqual((why["hi_probe"]["planned"], why["hi_probe"]["taken"]), (3, ["P1", "P2", "P3"]))

    def test_held_probe_units_count_against_the_three(self):
        buys, why = self.plan(inventory([unit("P1", 1500000), unit("P2", 1500000)]))
        q = self.qty(buys)
        self.assertEqual((q.get("P1"), q.get("P2"), q.get("P3")), (None, None, 1))
        self.assertEqual((why["hi_probe"]["held"], why["hi_probe"]["planned"]), (2, 1))

    def test_probe_places_sit_outside_the_total_cap(self):
        full = [unit(f"c{i % 6}") for i in range(10)]  # 10 counted toward a total of 10
        buys, why = self.plan(inventory(full), band={**BAND, "sales": {}})
        self.assertEqual(sum(self.qty(buys).get(k, 0) for k in ("P1", "P2", "P3")), 3)
        self.assertFalse(any(self.qty(buys).get(f"c{i}") for i in range(6)))

    def test_probe_units_do_not_take_band_places(self):
        _, why = self.plan(inventory([unit("P1", 1500000)]))
        self.assertEqual(why["hi_band"]["held"], 0)
        _, why = self.plan(inventory([unit("H", 1500000), unit("P1", 1500000)]))
        self.assertEqual(why["hi_band"]["held"], 1)

    def test_the_band_cap_bounds_the_probe(self):
        # Worst cost 1.5M a unit: the band's H takes 3 places (4.5M); a 7M cap leaves room for one probe unit.
        buys, why = self.plan(inventory(), band={**BAND, "max_worst_cost_total": 7000000})
        q = self.qty(buys)
        self.assertEqual(q.get("H"), 3)
        self.assertEqual(sum(q.get(k, 0) for k in ("P1", "P2", "P3")), 1)
        self.assertLessEqual(why["hi_probe"]["worst_cost_total"], 7000000)

    def test_the_run_budget_bounds_the_probe(self):
        # 5% of 40M is a 2M budget: one unit at 1.5M worst cost (plus a week of storage) fits, a second does not.
        buys, _ = self.plan(inventory(balance=40000000), band={**BAND, "sales": {}})
        self.assertEqual(sum(self.qty(buys).get(k, 0) for k in ("P1", "P2", "P3")), 1)

    def test_a_surged_probe_sku_keeps_the_median_test(self):
        surge = [{"sku": "P1", "units": 10, "factor": 1.2}]
        intel = {**{f"c{i}": CHEAP for i in range(6)}, **{k: HI_LOW for k in ("H", "P2", "P3", "P4")}}
        buys, _ = self.plan(inventory(surge=surge), intel=intel)  # P1 surged, no sampled median
        self.assertNotIn("P1", self.qty(buys))
        high = {**intel, "P1": dict(HI_LOW, median_price=1900000)}  # 1.9M clears 2M x .75 x 1.2 x 1.01
        buys, _ = self.plan(inventory(surge=surge), intel=high)
        self.assertEqual(self.qty(buys).get("P1"), 1)
        buys, _ = self.plan(inventory(surge=surge))  # P1 surged with a median under its surged worst cost
        self.assertNotIn("P1", self.qty(buys))

    def test_the_ordinary_pass_never_buys_a_probe_sku(self):
        passing = {**{f"c{i}": CHEAP for i in range(6)}, "H": HI_LOW, "P1": dict(HI_LOW, median_price=1900000), "P2": HI_LOW, "P3": HI_LOW}
        buys, _ = self.plan(inventory([unit("P1", 1500000)]), intel=passing)
        self.assertNotIn("P1", self.qty(buys))

    def test_probe_lines_leave_the_ordinary_lines_alone(self):
        buys, _ = self.plan(inventory(), band={**BAND, "sales": {}})
        ordinary = [b for b in buys if b["sku"].startswith("c")]
        self.assertEqual(len(ordinary), LIMITS["max_lines_per_run"])


class Settings(unittest.TestCase):
    def test_shipped_on_with_the_three_skus(self):
        # Switched on 11-10 after the approval; the code was committed off first.
        value = json.loads(hi_probe.CONFIG.read_text(encoding="utf-8"))
        self.assertIs(value["enabled"], True)
        self.assertEqual(hi_probe.settings(), {"skus": ["G03d0199f", "G24f5f979", "Gb5d0dde7"], "max_units": 3})

    def test_only_a_valid_file_turns_it_on(self):
        good = {"enabled": True, "skus": ["G03d0199f", "G24f5f979", "Gb5d0dde7"], "max_units": 3}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps(good), encoding="utf-8")
            self.assertEqual(hi_probe.settings(path), {"skus": good["skus"], "max_units": 3})
            for bad in ({**good, "enabled": "true"}, {**good, "enabled": False}, {**good, "skus": good["skus"] + ["G0e4deffc"]}, {**good, "skus": []},
                        {**good, "skus": ["G03d0199f", "G03d0199f"]}, {**good, "skus": ["H1"]}, {**good, "max_units": 4}, {**good, "max_units": 0},
                        {**good, "max_units": 3.0}, {k: v for k, v in good.items() if k != "max_units"}, []):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNone(hi_probe.settings(path), bad)
            self.assertIsNone(hi_probe.settings(Path(folder) / "missing.json"))

    def test_a_sku_leaves_the_probe_at_its_first_sale_of_ours(self):
        cat = {"G03d0199f": {"kind": "goods"}, "G24f5f979": {"kind": "goods"}, "Gb5d0dde7": {"kind": "food"}}
        good = {"enabled": True, "skus": ["G03d0199f", "G24f5f979", "Gb5d0dde7"], "max_units": 3}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps(good), encoding="utf-8")
            with patch.object(mb, "confirmed_counts", return_value={"G03d0199f": (1, -5)}):
                self.assertEqual(hi_probe.for_run(folder, NOW, cat, path), {"skus": ["G24f5f979"], "max_units": 3})
            with patch.object(mb, "confirmed_counts", return_value=None):
                self.assertIsNone(hi_probe.for_run(folder, NOW, cat, path))


if __name__ == "__main__":
    unittest.main()
