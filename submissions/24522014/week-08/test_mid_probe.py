"""Mid-price goods probe (mid_probe.json), approved by the user on 11-10: G0667d440 and G75f11c19, at most 3 held plus planned each,
inside the total cap, the run's units and the ordinary lines; bought only while the public median is above our typical goods cost
(.60 x list x surge), no more of a SKU while a held unit of it is 6 h old unsold (11-11; it replaced the arrival-cost stop), no
more of a SKU held 24 h without a sale; held plus planned worst cost within 1M. Off by default."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import mid_probe as mp
import restock_policy as rp

NOW = 1793800000.0
GOOD = {"enabled": True, "skus": ["G0667d440", "G75f11c19"], "units_per_sku": 3, "max_worst_cost_total": 1000000}
LIST = {"G0667d440": 347850, "G75f11c19": 122900}
# Public medians as sampled on 11-10 (.68 and .67 of list): above the typical cost (.60), below the worst cost (.75).
INTEL = {"G0667d440": {"median_price": 236660, "distinct_buyers": 26, "settles_6h": 59, "distinct_buyers_6h": 20},
         "G75f11c19": {"median_price": 82097, "distinct_buyers": 26, "settles_6h": 61, "distinct_buyers_6h": 14}}
RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 80, "max_proven_units_per_sku": 3, "max_units_per_run": 12, "max_lines_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}


def catalog():
    out = {s: {"kind": "goods", "category": "c", "list_price": p} for s, p in LIST.items()}
    out.update({f"g{i}": {"kind": "goods", "category": "c", "list_price": 100} for i in range(8)})
    return out


def unit(sku, cost=150000, age=3600):
    return {"unit": f"u-{sku}-{cost}-{age}", "sku": sku, "kind": "goods", "grade": "A", "cost": cost, "supplied_at": NOW - age}


class Settings(unittest.TestCase):
    def test_shipped_on(self):
        value = json.loads(mp.CONFIG.read_text(encoding="utf-8"))
        self.assertIs(value["enabled"], True)  # switched on 11-10 after the install commit 2f019ac6
        self.assertEqual((value["skus"], value["units_per_sku"], value["max_worst_cost_total"]), (GOOD["skus"], 3, 1000000))
        self.assertEqual(mp.settings(), {"skus": GOOD["skus"], "units_per_sku": 3, "max_worst_cost_total": 1000000})

    def test_only_a_valid_file_turns_it_on(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "m.json"
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertEqual(mp.settings(path), {"skus": GOOD["skus"], "units_per_sku": 3, "max_worst_cost_total": 1000000})
            for bad in ({**GOOD, "enabled": "true"}, {**GOOD, "skus": GOOD["skus"] + ["G0e4deffc"]}, {**GOOD, "skus": ["G0e4deffc"]}, {**GOOD, "skus": []},
                        {**GOOD, "skus": ["G0667d440"] * 2}, {**GOOD, "units_per_sku": 4}, {**GOOD, "units_per_sku": 0}, {**GOOD, "units_per_sku": 2.0},
                        {**GOOD, "max_worst_cost_total": 1000001}, {**GOOD, "max_worst_cost_total": 0}, {**GOOD, "max_worst_cost_total": "1"},
                        {k: v for k, v in GOOD.items() if k != "units_per_sku"}, []):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNone(mp.settings(path), bad)


class Picks(unittest.TestCase):
    def run_for(self, inv, issued=NOW - 3600, intel=INTEL, offers=(), sales=(), setting=GOOD, broken=False):
        with TemporaryDirectory() as folder:
            md = Path(folder)
            (md / "signed-offers").mkdir()
            (md / "checkout-events").mkdir()
            for i, (sku, cost) in enumerate(offers):
                (md / "signed-offers" / f"o{i}.json").write_text(json.dumps({"sku": sku, "cost": cost, "price": cost + 1, "task": f"t{i}", "expires_at": NOW - 999999, "issued_at": issued}), encoding="utf-8")
            for i, sku in enumerate(sales):
                task = f"s{i}"
                (md / "signed-offers" / f"s{i}.json").write_text(json.dumps({"sku": sku, "cost": 60000, "price": 90000, "task": task, "expires_at": NOW - 600, "issued_at": NOW - 900}), encoding="utf-8")
                (md / "checkout-events" / (hashlib.sha256(task.encode()).hexdigest() + ".json")).write_text(json.dumps({"status": "completed", "at": NOW - 700}), encoding="utf-8")
            if broken:
                (md / "signed-offers" / "bad.json").write_text("not json", encoding="utf-8")
            path = md / "m.json"
            path.write_text(json.dumps(setting), encoding="utf-8")
            with patch.object(mp, "ARRIVAL_SINCE", NOW - 864000):
                return mp.for_run(md, inv, catalog(), intel, NOW, path)

    @staticmethod
    def count(picks):
        out = {}
        for p in picks: out[p["sku"]] = out.get(p["sku"], 0) + 1
        return out

    def test_off_buys_nothing(self):
        self.assertEqual(self.run_for({"units": []}, setting={**GOOD, "enabled": False}), ([], None))

    def test_three_and_two_within_the_1m_worst_cost(self):
        # 3 x 347,850 x .75 = 782,662.5; a bedding unit is 92,175, so two fit under 1M and a third does not.
        picks, notes = self.run_for({"units": []})
        self.assertEqual(self.count(picks), {"G0667d440": 3, "G75f11c19": 2})
        self.assertEqual(notes, {"G0667d440": "ok", "G75f11c19": "worst_cost_cap"})
        self.assertAlmostEqual(picks[0]["estimated_cost"], 347850 * .75)
        self.assertTrue(all(p["probe"] for p in picks))

    def test_held_units_count_against_each_sku_and_the_cap(self):
        picks, notes = self.run_for({"units": [unit("G0667d440"), unit("G0667d440"), unit("G0667d440"), unit("G75f11c19", cost=60000)]})
        self.assertEqual(self.count(picks), {"G75f11c19": 1})
        self.assertEqual(notes["G0667d440"], "held_full")

    def test_a_median_at_or_below_the_typical_cost_stops_it(self):
        at = {s: {**v, "median_price": LIST[s] * .60} for s, v in INTEL.items()}
        self.assertEqual(self.run_for({"units": []}, intel=at)[0], [])
        self.assertEqual(self.run_for({"units": []}, intel={})[1], {"G0667d440": "median_not_above_typical_cost", "G75f11c19": "median_not_above_typical_cost"})

    def test_a_surge_raises_the_typical_cost(self):
        picks, notes = self.run_for({"units": [], "surge": [{"sku": "G0667d440", "factor": 1.2}]})
        self.assertNotIn("G0667d440", self.count(picks))
        self.assertEqual(notes["G0667d440"], "median_not_above_typical_cost")

    def test_a_unit_that_arrived_at_or_above_the_median_no_longer_ends_that_sku(self):
        # The user's 11-11 change: arrival cost alone never stops a SKU; the cost floor keeps every sale above cost.
        self.assertEqual(mp.ARRIVAL_SINCE, 1794306968.0)
        for inv, offers in (({"units": [unit("G0667d440", cost=236660)]}, ()), ({"units": []}, (("G0667d440", 240000),))):
            picks, notes = self.run_for(inv, offers=offers)
            self.assertIn("G0667d440", self.count(picks))
            self.assertNotEqual(notes["G0667d440"], "a_unit_arrived_at_or_above_the_median")
            self.assertIn("G75f11c19", self.count(picks))

    def test_a_unit_held_6h_unsold_stops_buying_that_sku_until_it_is_gone(self):
        self.assertEqual(mp.UNSOLD_SECONDS, 21600)
        picks, notes = self.run_for({"units": [unit("G75f11c19", cost=60000, age=21600)]})
        self.assertNotIn("G75f11c19", self.count(picks))
        self.assertEqual(notes["G75f11c19"], "a_unit_held_6h_unsold")
        self.assertIn("G0667d440", self.count(picks))
        # Recent sales of the SKU do not lift it while that unit is still held.
        _, notes = self.run_for({"units": [unit("G75f11c19", cost=60000, age=21600)]}, sales=("G75f11c19",))
        self.assertEqual(notes["G75f11c19"], "a_unit_held_6h_unsold")
        picks, notes = self.run_for({"units": [unit("G75f11c19", cost=60000, age=21599)]})
        self.assertNotEqual(notes["G75f11c19"], "a_unit_held_6h_unsold")
        self.assertIn("G75f11c19", self.count(picks))
        picks, notes = self.run_for({"units": []})
        self.assertIn("G75f11c19", self.count(picks))

    def test_a_unit_held_24h_without_a_sale_ends_buying_it(self):
        stale = {"units": [unit("G75f11c19", cost=60000, age=86400)]}
        picks, notes = self.run_for(stale)
        self.assertNotIn("G75f11c19", self.count(picks))
        self.assertEqual(notes["G75f11c19"], "held_24h_without_a_sale")
        # With a recent sale the 24 h rule passes, but that unit is still over 6 h unsold, so the SKU stays stopped.
        picks, notes = self.run_for(stale, sales=("G75f11c19",))
        self.assertNotIn("G75f11c19", self.count(picks))
        self.assertEqual(notes["G75f11c19"], "a_unit_held_6h_unsold")

    def test_an_unreadable_offer_buys_nothing(self):
        picks, notes = self.run_for({"units": []}, broken=True)
        self.assertEqual(picks, [])
        self.assertEqual(set(notes.values()), {"records_unreadable"})


class Plan(unittest.TestCase):
    def plan(self, picks, units=(), limits=LIMITS):
        cat = catalog()
        intel = {**{k: {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 95} for k in cat if k.startswith("g")}, **INTEL}
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan({"round": 3, "balance": 1000000000, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": list(units), "surge": []},
                           cat, [{"category": "c"}], {}, 0, intelligence=intel, runtime=RUNTIME, now=NOW, mid_probe=picks)

    def picks(self):
        return [{"sku": "G0667d440", "estimated_cost": 260887.5, "probe": True}] * 3 + [{"sku": "G75f11c19", "estimated_cost": 92175.0, "probe": True}] * 2

    def test_probe_goods_take_ordinary_lines_run_units_and_storage(self):
        buys, why = self.plan(self.picks())
        q = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual((q["G0667d440"], q["G75f11c19"]), (3, 2))
        self.assertEqual(why["mid_probe"], ["G0667d440"] * 3 + ["G75f11c19"] * 2)
        self.assertEqual(why["kit_lines"]["goods_probe"], 2)
        self.assertLessEqual(len([s for s in q if s.startswith("g")]), LIMITS["max_lines_per_run"] - 2)
        self.assertLessEqual(why["planned_units"], LIMITS["max_units_per_run"])
        self.assertGreaterEqual(why["new_stock_seven_day_storage"], (3 * 260887.5 + 2 * 92175.0) * .01 * 7)

    def test_they_count_toward_the_total_cap(self):
        held = [{"unit": f"h{i}", "sku": f"g{i % 8}", "kind": "goods", "grade": "A", "cost": 50} for i in range(78)]
        buys, why = self.plan(self.picks(), units=held)
        self.assertEqual(sum(b["qty"] for b in buys), 2)

    def test_the_ordinary_pass_never_deepens_a_probe_sku(self):
        buys, _ = self.plan([{"sku": "G75f11c19", "estimated_cost": 92175.0, "probe": True}])
        self.assertEqual({b["sku"]: b["qty"] for b in buys}["G75f11c19"], 1)

    def test_without_picks_the_plan_is_today(self):
        self.assertEqual(self.plan(None), self.plan([]))


if __name__ == "__main__":
    unittest.main()
