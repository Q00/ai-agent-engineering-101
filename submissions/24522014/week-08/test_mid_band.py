"""Mid-price demand band (mid_band.json, "U3"), approved by the user on 11-10: twelve named 200k-1M goods SKUs, one unit each, the
mid-price probe's buying rules plus no surged SKU, held plus planned worst cost within 6M, at most 4 ordinary lines a run, after the
1M+ probe and before the ordinary pass, inside the total cap and the run's units. Off by default."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import mid_band as mb
import restock_policy as rp

NOW = 1793800000.0
SKUS = list(mb.ALLOWED)
GOOD = {"enabled": True, "skus": SKUS, "units_per_sku": 1, "max_worst_cost_total": 6000000, "max_lines_per_run": 4}
RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 80, "max_proven_units_per_sku": 3, "max_units_per_run": 12, "max_lines_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}


def catalog(price=600000):
    out = {s: {"kind": "goods", "category": "c", "list_price": price} for s in SKUS}
    out.update({f"g{i}": {"kind": "goods", "category": "c", "list_price": 100} for i in range(8)})
    return out


def intel(price=600000):
    # Public medians at .68 of list, as sampled on 11-10: above the typical cost (.60), below the ordinary pass's worst cost (.75).
    return {s: {"median_price": price * .68, "distinct_buyers": 20, "settles_6h": 20, "distinct_buyers_6h": 12} for s in SKUS}


def unit(sku, cost=300000, age=3600):
    return {"unit": f"u-{sku}-{cost}-{age}", "sku": sku, "kind": "goods", "grade": "A", "cost": cost, "supplied_at": NOW - age}


class Settings(unittest.TestCase):
    def test_shipped_on(self):
        value = json.loads(mb.CONFIG.read_text(encoding="utf-8"))
        self.assertIs(value["enabled"], True)  # switched on 11-10 after the install commit cc24e1db; 3 a SKU and 17M from 11-11 (23786019)
        # At most 2 lines a run from 11-11 11:3x KST, a tightening after the 11:21 FALLBACK_B signal.
        self.assertEqual((value["skus"], value["units_per_sku"], value["max_worst_cost_total"], value["max_lines_per_run"]), (SKUS, 3, 17000000, 2))
        self.assertEqual(mb.settings()["max_lines_per_run"], 2)

    def test_only_a_valid_file_turns_it_on(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "b.json"
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertEqual(mb.settings(path), {"skus": SKUS, "units_per_sku": 1, "max_worst_cost_total": 6000000, "max_lines_per_run": 4})
            # Up to 3 a SKU and 17M, approved by the user on 11-11.
            three = {**GOOD, "units_per_sku": 3, "max_worst_cost_total": 17000000}
            path.write_text(json.dumps(three), encoding="utf-8")
            self.assertEqual(mb.settings(path), {"skus": SKUS, "units_per_sku": 3, "max_worst_cost_total": 17000000, "max_lines_per_run": 4})
            for bad in ({**GOOD, "enabled": "true"}, {**GOOD, "skus": SKUS + ["G0667d440"]}, {**GOOD, "skus": ["G0e4deffc"]}, {**GOOD, "skus": []},
                        {**GOOD, "skus": [SKUS[0]] * 2}, {**GOOD, "units_per_sku": 4}, {**GOOD, "units_per_sku": 0}, {**GOOD, "units_per_sku": True},
                        {**GOOD, "units_per_sku": 1.0}, {**three, "units_per_sku": 3.0},
                        {**GOOD, "max_worst_cost_total": 17000001}, {**GOOD, "max_worst_cost_total": 0}, {**GOOD, "max_lines_per_run": 5},
                        {**GOOD, "max_lines_per_run": 0}, {k: v for k, v in GOOD.items() if k != "max_lines_per_run"}, []):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNone(mb.settings(path), bad)


class Picks(unittest.TestCase):
    def run_for(self, inv, issued=NOW - 3600, price=600000, offers=(), sales=(), setting=GOOD, market=None):
        with TemporaryDirectory() as folder:
            md = Path(folder)
            (md / "signed-offers").mkdir()
            (md / "checkout-events").mkdir()
            for i, (sku, cost) in enumerate(offers):
                (md / "signed-offers" / f"o{i}.json").write_text(json.dumps({"sku": sku, "cost": cost, "price": cost + 1, "task": f"t{i}", "expires_at": NOW - 999999, "issued_at": issued}), encoding="utf-8")
            for i, sku in enumerate(sales):
                task = f"s{i}"
                (md / "signed-offers" / f"s{i}.json").write_text(json.dumps({"sku": sku, "cost": 300000, "price": 400000, "task": task, "expires_at": NOW - 600, "issued_at": NOW - 900}), encoding="utf-8")
                (md / "checkout-events" / (hashlib.sha256(task.encode()).hexdigest() + ".json")).write_text(json.dumps({"status": "completed", "at": NOW - 700}), encoding="utf-8")
            path = md / "b.json"
            path.write_text(json.dumps(setting), encoding="utf-8")
            with patch.object(mb, "ARRIVAL_SINCE", NOW - 864000):
                return mb.for_run(md, inv, catalog(price), intel(price) if market is None else market, NOW, path)

    def test_off_is_none(self):
        self.assertIsNone(self.run_for({"units": []}, setting={**GOOD, "enabled": False}))

    def test_one_of_each_within_6m(self):
        got = self.run_for({"units": []})  # 12 x 450,000 worst = 5.4M
        self.assertEqual([p["sku"] for p in got["picks"]], SKUS)
        self.assertEqual(got["max_lines"], 4)
        got = self.run_for({"units": []}, price=700000)  # 525,000 worst each: 11 fit under 6M
        self.assertEqual(len(got["picks"]), 11)
        self.assertEqual(got["notes"][SKUS[-1]], "worst_cost_cap")

    def test_three_of_each_within_17m(self):
        three = {**GOOD, "units_per_sku": 3, "max_worst_cost_total": 17000000}
        got = self.run_for({"units": []}, setting=three)  # 36 x 450,000 worst = 16.2M
        self.assertEqual([p["sku"] for p in got["picks"]], [s for s in SKUS for _ in range(3)])
        got = self.run_for({"units": []}, setting=three, price=700000)  # 525,000 worst each: 32 fit under 17M
        self.assertEqual(len(got["picks"]), 32)
        self.assertEqual((got["notes"][SKUS[10]], got["notes"][SKUS[11]]), ("worst_cost_cap", "worst_cost_cap"))
        self.assertEqual([p["sku"] for p in got["picks"]].count(SKUS[10]), 2)
        got = self.run_for({"units": [unit(SKUS[0]), unit(SKUS[1]), unit(SKUS[1]), unit(SKUS[1])]}, setting=three)
        self.assertEqual([p["sku"] for p in got["picks"]].count(SKUS[0]), 2)
        self.assertEqual(got["notes"][SKUS[1]], "held_full")
        self.assertNotIn(SKUS[1], [p["sku"] for p in got["picks"]])

    def test_held_counts_and_a_surged_sku_is_skipped(self):
        got = self.run_for({"units": [unit(SKUS[0])], "surge": [{"sku": SKUS[1], "factor": 1.05}]})
        self.assertEqual(got["notes"][SKUS[0]], "held_full")
        self.assertEqual(got["notes"][SKUS[1]], "surged")
        self.assertEqual([p["sku"] for p in got["picks"]], SKUS[2:])

    def test_arrival_cost_no_longer_stops_a_sku(self):
        self.assertEqual(mb.ARRIVAL_SINCE, 1794308090.0)  # U3 on, 11-10 19:54:50 KST
        for issued in (NOW - 900000, NOW - 900):
            got = self.run_for({"units": []}, offers=((SKUS[1], 600000 * .7),), issued=issued)
            self.assertEqual(got["notes"][SKUS[1]], "ok")
            self.assertIn(SKUS[1], [p["sku"] for p in got["picks"]])

    def test_the_probe_rules_apply(self):
        low = {**intel(), SKUS[0]: {"median_price": 600000 * .60}}
        got = self.run_for({"units": []}, market=low)
        self.assertEqual(got["notes"][SKUS[0]], "median_not_above_typical_cost")
        unsold = {"units": [unit(SKUS[1], age=21600)]}
        self.assertEqual(self.run_for(unsold)["notes"][SKUS[1]], "a_unit_held_6h_unsold")
        self.assertEqual(self.run_for({"units": [unit(SKUS[1], age=21599)]})["notes"][SKUS[1]], "held_full")
        stale = {"units": [unit(SKUS[2], age=86400)]}
        self.assertEqual(self.run_for(stale)["notes"][SKUS[2]], "held_24h_without_a_sale")
        self.assertEqual(self.run_for(stale, sales=(SKUS[2],))["notes"][SKUS[2]], "a_unit_held_6h_unsold")


class Plan(unittest.TestCase):
    def plan(self, band, units=(), mid=None, limits=LIMITS):
        cat = catalog()
        market = {**{k: {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 95} for k in cat if k.startswith("g")}, **intel()}
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan({"round": 3, "balance": 1000000000, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": list(units), "surge": []},
                           cat, [{"category": "c"}], {}, 0, intelligence=market, runtime=RUNTIME, now=NOW, mid_probe=mid, mid_band=band)

    def band(self, n=12, lines=4, units=1):
        return {"picks": [{"sku": s, "estimated_cost": 450000.0, "probe": True} for s in SKUS[:n] for _ in range(units)], "max_lines": lines, "notes": {}}

    def test_three_a_sku_add_units_not_lines_within_the_run_units(self):
        buys, why = self.plan(self.band(units=3))
        q = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual(why["kit_lines"]["goods_band"], 4)
        self.assertEqual([q[s] for s in SKUS[:4]], [3, 3, 3, 3])
        self.assertTrue(all(s not in q for s in SKUS[4:]))
        self.assertLessEqual(why["planned_units"], LIMITS["max_units_per_run"])
        self.assertLessEqual(why["kit_lines"]["total"], rp.KIT_LINES)
        held = [{"unit": f"h{i}", "sku": f"g{i % 8}", "kind": "goods", "grade": "A", "cost": 50} for i in range(75)]
        buys, why = self.plan(self.band(units=3), units=held)  # 5 left under the 80 total
        self.assertEqual(sum(b["qty"] for b in buys), 5)
        self.assertEqual(why["mid_band"], [SKUS[0]] * 3 + [SKUS[1]] * 2)

    def test_at_most_its_lines_and_the_ordinary_pass_keeps_lines(self):
        buys, why = self.plan(self.band())
        q = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual(why["mid_band"], SKUS[:4])
        self.assertEqual(why["kit_lines"]["goods_band"], 4)
        self.assertTrue(all(q[s] == 1 for s in SKUS[:4]))
        self.assertGreater(len([s for s in q if s.startswith("g")]), 0)
        self.assertLessEqual(len(q), rp.KIT_LINES)
        self.assertGreaterEqual(why["new_stock_seven_day_storage"], 4 * 450000 * .01 * 7)

    def test_room_goes_to_it_before_the_ordinary_pass(self):
        held = [{"unit": f"h{i}", "sku": f"g{i % 8}", "kind": "goods", "grade": "A", "cost": 50} for i in range(77)]
        buys, why = self.plan(self.band(), units=held)
        self.assertEqual(why["mid_band"], SKUS[:3])
        self.assertEqual(sum(b["qty"] for b in buys), 3)

    def test_after_the_mid_probe_inside_the_run_units(self):
        mid = [{"sku": "G0667d440", "estimated_cost": 260887.5, "probe": True}] * 3
        buys, why = self.plan(self.band(), mid=mid)
        self.assertEqual(why["mid_probe"], ["G0667d440"] * 3)
        self.assertEqual(why["mid_band"], SKUS[:4])
        self.assertLessEqual(why["planned_units"], LIMITS["max_units_per_run"])
        self.assertLessEqual(why["kit_lines"]["total"], rp.KIT_LINES)

    def test_the_ordinary_pass_never_adds_a_band_sku(self):
        buys, _ = self.plan(self.band(n=1))
        self.assertEqual({b["sku"]: b["qty"] for b in buys}[SKUS[0]], 1)

    def test_without_it_the_plan_is_today(self):
        self.assertEqual(self.plan(None), self.plan({"picks": [], "max_lines": 4, "notes": {}}))


if __name__ == "__main__":
    unittest.main()
