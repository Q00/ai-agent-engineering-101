"""Food probe (food_probe.json), approved by the user on 11-10: up to 5 named food SKUs, up to 3 units each held at a time, never sold
below cost x 1.08 (seller side). Off by default; buys nothing until the running seller reports food_floor_108_v1. Units count toward
the run's units, the total cap and the ordinary lines, as the food trial's do."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import food_probe as fp
import restock_policy as rp

RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 80, "max_proven_units_per_sku": 3, "max_units_per_run": 12, "max_lines_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}
NOW = 1793800000.0
GOOD = {"enabled": True, "skus": ["G4a998429", "Gdf79cd07", "G476cc094", "G80435d53"], "units_per_sku": 3, "max_total_estimated_cost": 310000}
CHEAP = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 95}


def food(price=20000):
    return {"kind": "food", "category": "f", "list_price": price, "shelf_hours": 168}


def catalog():
    out = {s: food() for s in GOOD["skus"]}
    out.update({f"g{i}": {"kind": "goods", "category": "c", "list_price": 100} for i in range(8)})
    return out


def unit(sku, grade="A"):
    return {"unit": f"u-{sku}", "sku": sku, "kind": "food", "grade": grade, "cost": 10000}


class Settings(unittest.TestCase):
    def test_shipped_on(self):
        # On 11-10 12:04 with the food floor, off 11-10 19:50:25 KST (net negative on stars), on again 11-11 about 18:0x KST (the
        # user's choice in the Orchestrator session) with the same SKUs, units and cost limit; at most 4 picks a run from 11-11 20:18:17
        # KST (an Orchestrator tightening within the user's food approval).
        value = json.loads(fp.CONFIG.read_text(encoding="utf-8"))
        self.assertIs(value["enabled"], True)
        self.assertEqual((value["skus"], value["units_per_sku"], value["max_total_estimated_cost"]), (GOOD["skus"], 3, 310000))
        self.assertEqual(fp.settings()["max_units_per_run"], 4)

    def test_only_a_valid_file_turns_it_on(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "f.json"
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertEqual(fp.settings(path)["units_per_sku"], 3)
            for bad in ({**GOOD, "enabled": "true"}, {**GOOD, "skus": GOOD["skus"] + ["G0e4deffc", "G66ded25b"]}, {**GOOD, "skus": []}, {**GOOD, "skus": ["G4a998429"] * 2},
                        {**GOOD, "skus": ["x"]}, {**GOOD, "units_per_sku": 4}, {**GOOD, "units_per_sku": 0}, {**GOOD, "max_total_estimated_cost": 400001},
                        {**GOOD, "max_total_estimated_cost": 0}, {k: v for k, v in GOOD.items() if k != "units_per_sku"}, []):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNone(fp.settings(path), bad)

    def test_max_units_per_run_is_optional_and_checked(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "f.json"
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertNotIn("max_units_per_run", fp.settings(path))  # absent: today's picks
            for good in (1, 4, 12):
                path.write_text(json.dumps({**GOOD, "max_units_per_run": good}), encoding="utf-8")
                self.assertEqual(fp.settings(path)["max_units_per_run"], good)
            for bad in (0, 13, -1, 4.0, "4", True, None, [4]):
                path.write_text(json.dumps({**GOOD, "max_units_per_run": bad}), encoding="utf-8")
                self.assertIsNone(fp.settings(path), bad)


class Picks(unittest.TestCase):
    def run_for(self, inv, features=("food_floor_108_v1",), setting=GOOD, multiple=1.08):
        with TemporaryDirectory() as folder:
            Path(folder, "strategy-consumer.json").write_text(json.dumps({"features": list(features)}), encoding="utf-8")
            policy = Path(folder) / "r3_policy.json"
            policy.write_text(json.dumps({"selling": {"food_pricing_v2": {"floor_cost_multiple": multiple}}}), encoding="utf-8")
            path = Path(folder) / "f.json"
            path.write_text(json.dumps(setting), encoding="utf-8")
            return fp.for_run(folder, inv, catalog(), path, policy)

    def test_waits_for_the_seller_floor(self):
        self.assertEqual(self.run_for({"units": []}, features=("food_pricing_v2",)), ([], "food_floor_108_v1"))

    def test_waits_until_the_floor_is_on_at_108_or_more(self):
        for multiple in (None, 1.0, 1.07, "1.08", 1.6):
            with self.subTest(multiple=multiple):
                self.assertEqual(self.run_for({"units": []}, multiple=multiple), ([], "food_floor_108_v1"))
        self.assertTrue(self.run_for({"units": []}, multiple=1.1)[0])

    def test_the_shipped_policy_has_the_floor_on(self):
        self.assertTrue(fp.floor_on())

    def test_three_of_each_less_what_is_held(self):
        picks, waiting = self.run_for({"units": [unit("G4a998429"), unit("G4a998429"), unit("Gdf79cd07", grade="expired")]})
        self.assertIsNone(waiting)
        count = {}
        for p in picks: count[p["sku"]] = count.get(p["sku"], 0) + 1
        self.assertEqual(count, {"G4a998429": 1, "Gdf79cd07": 3, "G476cc094": 3, "G80435d53": 3})

    def test_the_cost_cap_counts_held_stock(self):
        # 15000 worst cost a unit; 100000 holds 6 units, 2 of them already held.
        picks, _ = self.run_for({"units": [unit("G4a998429"), unit("Gdf79cd07")]}, setting={**GOOD, "max_total_estimated_cost": 100000})
        self.assertEqual(len(picks), 4)

    def test_surged_and_non_food_skus_are_skipped(self):
        picks, _ = self.run_for({"units": [], "surge": [{"sku": "G4a998429", "factor": 1.2}]})
        self.assertNotIn("G4a998429", {p["sku"] for p in picks})

    def test_without_the_per_run_cap_the_order_is_sku_by_sku(self):
        picks, _ = self.run_for({"units": []})
        self.assertEqual([p["sku"] for p in picks], [s for s in GOOD["skus"] for _ in range(3)])

    def test_per_run_cap_interleaves_and_truncates(self):
        picks, waiting = self.run_for({"units": []}, setting={**GOOD, "max_units_per_run": 4})
        self.assertIsNone(waiting)
        self.assertEqual([p["sku"] for p in picks], GOOD["skus"])  # one of each, not three of the first
        picks, _ = self.run_for({"units": []}, setting={**GOOD, "max_units_per_run": 6})
        self.assertEqual([p["sku"] for p in picks], GOOD["skus"] + GOOD["skus"][:2])

    def test_per_run_cap_skips_what_is_held_and_keeps_the_cost_limit(self):
        s = GOOD["skus"]
        # Held: 3 of the first (full), 1 of the second; wants are 0, 2, 3, 3.
        held = [unit(s[0]), unit(s[0]), unit(s[0]), unit(s[1])]
        picks, _ = self.run_for({"units": held}, setting={**GOOD, "max_units_per_run": 12})
        self.assertEqual([p["sku"] for p in picks], [s[1], s[2], s[3], s[1], s[2], s[3], s[2], s[3]])
        # 15000 worst a unit; 100000 holds 6 with 4 held, so 2 more, one SKU at a time.
        picks, _ = self.run_for({"units": held}, setting={**GOOD, "max_total_estimated_cost": 100000, "max_units_per_run": 12})
        self.assertEqual([p["sku"] for p in picks], [s[1], s[2]])

    def test_per_run_cap_frees_run_units_for_ordinary(self):
        picks, _ = self.run_for({"units": []}, setting={**GOOD, "max_units_per_run": 4})
        buys, why = Plan().plan(picks)
        q = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual(sum(q.get(x, 0) for x in GOOD["skus"]), 4)
        self.assertEqual(why["kit_lines"]["food_probe"], 4)


class Plan(unittest.TestCase):
    def plan(self, picks, limits=LIMITS):
        cat = catalog()
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan({"round": 3, "balance": 1000000000, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": [], "surge": []},
                           cat, [{"category": "c"}, {"category": "f"}], {}, 0, intelligence={k: CHEAP for k in cat}, runtime=RUNTIME, now=NOW, food_probe=picks)

    def test_probe_food_takes_ordinary_lines_and_run_units(self):
        picks = [{"sku": s, "estimated_cost": 15000, "probe": True} for s in GOOD["skus"] for _ in range(3)]
        buys, why = self.plan(picks)
        q = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual(sum(q.get(s, 0) for s in GOOD["skus"]), 12)  # the run's 12 units
        self.assertEqual(why["kit_lines"]["food_probe"], 4)  # 3, 3, 3, 3 fill the units before a fifth line
        self.assertLessEqual(len([s for s in q if s.startswith("g")]), LIMITS["max_lines_per_run"] - 4)

    def test_without_picks_the_plan_is_today(self):
        self.assertEqual(self.plan(None), self.plan([]))


if __name__ == "__main__":
    unittest.main()
