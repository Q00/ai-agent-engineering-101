"""The food trial stays inside its categories and caps, starts off, and is judged by its stop rules."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import analyze_food_trial as aft
import food_trial as ft
import restock_policy as rp

KIMCHI, MEAT = "식품>김치/반찬/즉석요리>김치", "식품>육류>가공/조리 육류"
ON = {"enabled": True, "trial_id": "v2", "categories": {KIMCHI: 2, MEAT: 2}, "max_total_units": 4, "max_total_estimated_cost": 150000}
HOT = {"settles_6h": 9, "distinct_buyers": 5, "distinct_buyers_6h": 5, "median_price": 100}
NOW = 1793800000.0
RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 40, "max_proven_units_per_sku": 3, "max_units_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}


def food(category=KIMCHI, price=20000, shelf=168):
    return {"kind": "food", "category": category, "list_price": price, "shelf_hours": shelf}


def fresh():
    return {"planned": {}, "units": {}}


class Settings(unittest.TestCase):
    def load(self, content):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "food_trial.json"
            if content is not None:
                path.write_text(content, encoding="utf-8")
            return ft.load_settings(path)

    def test_valid_settings_turn_on(self):
        self.assertEqual(self.load(json.dumps(ON)), ON)

    def test_missing_broken_or_excessive_settings_stay_off(self):
        for content in (None, "{broken", "[]", "{}", json.dumps({**ON, "enabled": False}), json.dumps({**ON, "enabled": "true"}),
                        json.dumps({**ON, "max_total_units": 5}), json.dumps({**ON, "max_total_estimated_cost": 150001}),
                        json.dumps({**ON, "categories": {KIMCHI: 5}}), json.dumps({**ON, "categories": {}}), json.dumps({"enabled": True})):
            with self.subTest(content=content):
                self.assertEqual(self.load(content), {"enabled": False})

    def test_shipped_file_keeps_the_approved_bounds(self):
        value = json.loads((ft.ROOT / "food_trial.json").read_text(encoding="utf-8"))
        self.assertEqual((value["categories"], value["max_total_units"], value["max_total_estimated_cost"]), ({KIMCHI: 2, MEAT: 2}, 4, 150000))


class Choosing(unittest.TestCase):
    def test_only_named_categories_with_public_sales_are_chosen(self):
        catalog = {"k1": food(), "k2": food(), "k3": food(), "m1": food(MEAT, shelf=720), "m2": food(MEAT, shelf=720), "fruit": food("식품>야채/과일>신선과일(부패주의)"),
                   "goods": {"kind": "goods", "category": KIMCHI, "list_price": 100}, "quiet": food()}
        intel = {k: HOT for k in catalog if k != "quiet"}
        picks = ft.choose(ON, fresh(), catalog, intel, [], NOW)
        chosen = {p["sku"] for p in picks}
        self.assertEqual(len(picks), 4)
        self.assertLessEqual({"m1", "m2"}, chosen)
        self.assertEqual(len(chosen & {"k1", "k2", "k3"}), 2)
        self.assertFalse(chosen & {"fruit", "goods", "quiet"})
        self.assertTrue(all("public sales" in p["reason"] for p in picks))

    def test_evidence_thresholds(self):
        catalog = {"k": food()}
        for signal in ({"settles_6h": 2, "distinct_buyers": 5}, {"settles_6h": 9, "distinct_buyers": 2}, {}):
            self.assertEqual(ft.choose(ON, fresh(), catalog, {"k": signal}, [], NOW), [])

    def test_surge_is_avoided_then_shelf_then_sales(self):
        catalog = {"surged": food(), "short": food(shelf=48), "long": food(shelf=720), "long2": food(shelf=720)}
        intel = {k: dict(HOT, settles_6h=50 if k == "surged" else 9) for k in catalog}
        intel["long2"] = dict(HOT, settles_6h=20)
        picks = ft.choose(dict(ON, categories={KIMCHI: 2}), fresh(), catalog, intel, [{"sku": "surged", "factor": 1.2}], NOW)
        self.assertEqual([p["sku"] for p in picks], ["long2", "long"])

    def test_surged_sku_costs_more_in_the_estimate(self):
        picks = ft.choose(dict(ON, categories={KIMCHI: 1}), fresh(), {"s": food(price=10000)}, {"s": HOT}, [{"sku": "s", "factor": 1.2}], NOW)
        self.assertAlmostEqual(picks[0]["estimated_cost"], 9000)

    def test_cumulative_four_units_never_refills_after_a_sale(self):
        catalog = {f"k{i}": food() for i in range(9)}
        state = fresh()
        state["planned"] = {f"k{i}": {"category": KIMCHI, "estimated_cost": 1} for i in range(2)}
        state["planned"].update({f"m{i}": {"category": MEAT, "estimated_cost": 1} for i in range(2)})
        catalog.update({f"m{i}": food(MEAT) for i in range(9)})
        self.assertEqual(ft.choose(ON, state, catalog, {k: HOT for k in catalog}, [], NOW), [])
        state["units"] = {}
        self.assertEqual(ft.choose(dict(ON, categories={KIMCHI: 2, MEAT: 2}), state, catalog, {k: HOT for k in catalog}, [], NOW), [])

    def test_category_share_counts_what_was_already_planned(self):
        catalog = {f"k{i}": food() for i in range(4)}
        state = fresh()
        state["planned"] = {"k0": {"category": KIMCHI, "estimated_cost": 1}}
        self.assertEqual(len(ft.choose(ON, state, catalog, {k: HOT for k in catalog}, [], NOW)), 1)

    def test_cost_cap_counts_estimates_and_skips_what_does_not_fit(self):
        catalog = {"big": food(price=210000), "small": food(price=20000)}
        picks = ft.choose(ON, fresh(), catalog, {k: HOT for k in catalog}, [], NOW)
        self.assertEqual([p["sku"] for p in picks], ["small"])
        state = fresh()
        state["planned"] = {"x": {"category": MEAT, "estimated_cost": 149000}}
        self.assertEqual(ft.choose(ON, state, {"small": food(price=20000)}, {"small": HOT}, [], NOW), [])

    def test_off_or_damaged_state_buys_nothing(self):
        catalog, intel = {"k": food()}, {"k": HOT}
        self.assertEqual(ft.choose({"enabled": False}, fresh(), catalog, intel, [], NOW), [])
        self.assertEqual(ft.choose(ON, None, catalog, intel, [], NOW), [])

    def test_damaged_state_file_reads_as_none(self):
        with TemporaryDirectory() as md:
            self.assertEqual(ft.load_state(md), fresh())
            for content in ("{broken", "[]", json.dumps({"planned": []})):
                ft.state_path(md).write_text(content, encoding="utf-8")
                self.assertIsNone(ft.load_state(md))


class PlanWiring(unittest.TestCase):
    def inv(self, units=(), balance=100000000, **changes):
        return {"round": 3, "balance": balance, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": list(units), **changes}

    def plan(self, inv, catalog, intel, food_picks, limits=LIMITS):
        with patch.object(rp, "capacity_config", return_value=limits):
            return rp.plan(inv, catalog, [{"category": c} for c in {v["category"] for v in catalog.values()}], {}, 0, intelligence=intel, runtime=RUNTIME, now=NOW, food_trial=food_picks)

    def test_without_a_trial_food_stays_out_and_goods_are_unchanged(self):
        catalog = {"f": food(), "g": {"kind": "goods", "category": "c", "list_price": 100}}
        self.assertEqual(self.plan(self.inv(), catalog, {k: HOT for k in catalog}, None)[0], [{"sku": "g", "qty": 3}])
        self.assertEqual(self.plan(self.inv(), catalog, {k: HOT for k in catalog}, [])[0], [{"sku": "g", "qty": 3}])

    def test_chosen_food_is_one_unit_each_inside_the_run_limit(self):
        catalog = {"f": food(), "f2": food(), "g": {"kind": "goods", "category": "c", "list_price": 100}, "g2": {"kind": "goods", "category": "d", "list_price": 100}}
        picks = [{"sku": "f", "estimated_cost": 15000}, {"sku": "f2", "estimated_cost": 15000}]
        buys, why = self.plan(self.inv(), catalog, {k: HOT for k in catalog}, picks)
        quantity = {b["sku"]: b["qty"] for b in buys}
        self.assertEqual((quantity["f"], quantity["f2"]), (1, 1))
        self.assertLessEqual(sum(quantity.values()), 6)
        self.assertEqual(why["food_trial"], ["f", "f2"])

    def test_food_counts_toward_total_stock(self):
        catalog = {"f": food(), "g": {"kind": "goods", "category": "c", "list_price": 100}}
        units = [{"sku": f"h{i}", "cost": 50, "grade": "A", "kind": "goods", "category": "a", "supplied_at": NOW} for i in range(39)]
        buys, _ = self.plan(self.inv(units), catalog, {k: HOT for k in catalog}, [{"sku": "f", "estimated_cost": 15000}])
        self.assertEqual(buys, [{"sku": "f", "qty": 1}])

    def test_food_respects_the_run_budget(self):
        catalog = {"f": food(price=1000000)}
        buys, why = self.plan(self.inv(balance=2000000), catalog, {"f": HOT}, [{"sku": "f", "estimated_cost": 750000}])
        self.assertEqual(buys, [])
        self.assertLess(why["budget"], 750000)

    def test_closed_window_buys_no_food(self):
        catalog = {"f": food()}
        buys, why = self.plan(self.inv(restock={"open": False}), catalog, {"f": HOT}, [{"sku": "f", "estimated_cost": 15000}])
        self.assertEqual((buys, why["reason"]), ([], "market_restock_window_closed"))


class Observing(unittest.TestCase):
    def test_arrivals_of_planned_food_are_kept(self):
        state = fresh()
        state["planned"]["k"] = {"category": KIMCHI}
        inv = {"units": [{"unit": "u1", "sku": "k", "kind": "food", "cost": 10, "grade": "B", "supplied_at": 5, "expires_at": 9}, {"unit": "u2", "sku": "g", "kind": "goods", "cost": 1}]}
        ft.observe(state, inv, NOW)
        self.assertEqual(list(state["units"]), ["u1"])
        self.assertEqual(state["units"]["u1"]["grade_on_arrival"], "B")
        state["units"]["u1"]["cost"] = 11
        ft.observe(state, inv, NOW + 1)
        self.assertEqual(state["units"]["u1"]["cost"], 11)


class Verdict(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".market/signed-offers").mkdir(parents=True)
        self.write(".market/catalog.json", {"g": {"kind": "goods"}, "k": {"kind": "food"}})
        for index, profit in enumerate((100, 200, 300)):
            self.write(f".market/signed-offers/g{index}.json", {"unit": f"g{index}", "sku": "g", "price": 1000 + profit, "cost": 1000})

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def setup_units(self, units, sales=(), held=()):
        self.write(".market/food-trial.json", {"planned": {"k": {"estimated_cost": 7}}, "units": units})
        self.write(".market/local-settlements.json", {"records": {str(i): s for i, s in enumerate(sales)}})
        self.write(".market/inventory.json", {"units": [{"unit": u} for u in held]})

    def test_no_arrivals_yet_gives_no_signal(self):
        self.setup_units({})
        result = aft.report(self.root, NOW)
        self.assertEqual((result["units_arrived"], result["stop"]), (0, False))

    def test_unsold_expired_unit_is_a_discard(self):
        self.setup_units({"u": {"sku": "k", "cost": 50, "supplied_at": NOW - 90000, "expires_at": NOW - 1}})
        self.assertIn("discarded_unsold", aft.report(self.root, NOW)["stop_signals"])

    def test_a_held_or_unexpired_unit_is_not_a_discard(self):
        self.setup_units({"u": {"sku": "k", "cost": 50, "supplied_at": NOW - 100, "expires_at": NOW + 9}})
        self.assertEqual(aft.report(self.root, NOW)["discards"], 0)

    def test_slow_sales_after_a_day_stop_the_trial(self):
        units = {f"u{i}": {"sku": "k", "cost": 50, "supplied_at": NOW - 90000, "expires_at": NOW + 999} for i in range(3)}
        self.setup_units(units, [{"unit": "u0", "amount": 500, "ts": NOW - 89000}], ["u1", "u2"])
        self.assertIn("sold_within_24h_below_half", aft.report(self.root, NOW)["stop_signals"])

    def test_half_sold_in_time_is_enough_and_young_units_are_not_judged(self):
        units = {"a": {"sku": "k", "cost": 50, "supplied_at": NOW - 90000, "expires_at": NOW + 999}, "b": {"sku": "k", "cost": 50, "supplied_at": NOW - 90000, "expires_at": NOW + 999},
                 "young": {"sku": "k", "cost": 50, "supplied_at": NOW - 100, "expires_at": NOW + 999}}
        self.setup_units(units, [{"unit": "a", "amount": 500, "ts": NOW - 89000}], ["b", "young"])
        self.assertNotIn("sold_within_24h_below_half", aft.report(self.root, NOW)["stop_signals"])

    def test_low_profit_compared_with_goods_stops_the_trial(self):
        self.setup_units({"a": {"sku": "k", "cost": 50, "supplied_at": NOW - 100, "expires_at": NOW + 999}}, [{"unit": "a", "amount": 99, "ts": NOW}])
        result = aft.report(self.root, NOW)
        self.assertEqual((result["profit_per_deal"], result["goods_profit_median"]), (49, 200))
        self.assertIn("profit_below_quarter_of_goods_median", result["stop_signals"])
        self.setup_units({"a": {"sku": "k", "cost": 50, "supplied_at": NOW - 100, "expires_at": NOW + 999}}, [{"unit": "a", "amount": 100, "ts": NOW}])
        self.assertEqual(aft.report(self.root, NOW)["stop_signals"], [])


if __name__ == "__main__":
    unittest.main()
