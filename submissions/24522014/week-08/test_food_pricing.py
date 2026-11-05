"""Food sells near the market price while an order can still use it and at cost once none can; the old seller gets no new stock."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

import food_trial as ft
import r3_policy
import seller_r3
from test_seller_fallback import Fixture

SHELF = 100
LIST = 1000
COST = 300
RULE = r3_policy.load_policy()["selling"]["food_pricing_v2"]
KIMCHI = "식품>김치/반찬/즉석요리>김치"


def terms(left, cost=COST, list_price=LIST, now=1000.0):
    unit = {"unit": "u1", "sku": "G1", "kind": "food", "grade": "C", "cost": cost, "list_price": list_price, "expires_at": now + SHELF * 3600 * left}
    return r3_policy.selling_terms(unit, {"G1": {"shelf_hours": SHELF}}, now=now, runtime={"version": 1, "controls": {"quote_multipliers": {}}})


class Rule(unittest.TestCase):
    def test_each_band(self):
        for left, floor, quote, stage, reason in ((.9, COST, LIST, 0, "standard"), (.45, COST, LIST, 0, "standard"), (.449, 600, 720, 0, "food_market_price"), (.25, 600, 720, 0, "food_market_price"),
                                                  (.249, 600, 700, 0, "food_market_price"), (.10, 600, 700, 0, "food_market_price"), (.099, COST, COST, 1, "food_expiry_clearance"), (.001, COST, COST, 1, "food_expiry_clearance")):
            with self.subTest(left=left):
                got = terms(left)
                self.assertEqual((got["floor"], got["quote"], got["discount_stage"], got["discount_reason"]), (floor, quote, stage, reason))

    def test_floor_is_never_below_cost(self):
        for left in (.9, .4, .2, .05, .001):
            for cost in (1, 250, 650, 750, 999):
                with self.subTest(left=left, cost=cost):
                    got = terms(left, cost=cost)
                    self.assertGreaterEqual(got["floor"], cost)
                    self.assertGreaterEqual(got["quote"], got["floor"])
                    self.assertFalse(got["below_cost_allowed"])

    def test_an_expensive_unit_holds_at_its_cost(self):
        got = terms(.3, cost=750)
        self.assertEqual((got["floor"], got["quote"]), (750, 750))

    def test_expired_is_still_refused(self):
        with self.assertRaises(ValueError):
            terms(-.01)
        with self.assertRaises(ValueError):
            terms(0)

    def test_goods_ignore_the_food_rule(self):
        unit = {"unit": "u1", "sku": "G1", "kind": "goods", "grade": "C", "cost": COST, "list_price": LIST, "supplied_at": 1000.0}
        got = r3_policy.selling_terms(unit, {"G1": {}}, now=1000.0, runtime={"version": 1, "controls": {"quote_multipliers": {}}})
        self.assertEqual((got["quote"], got["discount_reason"]), (LIST, "standard"))

    def test_settings_carry_their_evidence_and_keep_floors_under_quotes(self):
        self.assertEqual(RULE["clearance_below_left"], min(b["left_from"] for b in RULE["market_bands"]))
        self.assertIn("analyze_food_pricing.py", RULE["evidence"])
        for band in RULE["market_bands"]:
            self.assertLess(band["floor_share"], band["quote_share"])
            self.assertGreater(band["public_sales"], 0)


class Sale(Fixture):
    def setUp(self):
        super().setUp()
        self.food = {"unit": "u1", "sku": "G1", "name": "fixture", "kind": "food", "grade": "C", "cost": COST, "list_price": LIST,
                     "supplied_at": time.time(), "expires_at": time.time() + SHELF * 3600 * .3, "cert": {"x": 1}}
        self.inventory(self.food)
        (self.root / "catalog.json").write_text(json.dumps({"G1": {"kind": "food", "list_price": LIST, "shelf_hours": SHELF}}), encoding="utf8")

    async def test_opens_at_the_market_quote_with_condition_disclosed(self):
        await self.send("buy G1")
        self.assertEqual(self.last_ask(), 720)
        text = str(self.updater.requires_input.await_args.args[0])
        self.assertIn("Inventory condition: C", text)
        self.assertIn("Expires at", text)
        self.assertNotIn("Clearance", text)

    async def test_the_old_cost_price_is_not_signed(self):
        await self.send("buy G1", str(COST), str(COST), str(COST))
        self.signer.assert_not_called()
        self.assertGreaterEqual(self.last_ask(), 600)

    async def test_paying_the_quote_signs_with_the_real_grade_and_no_warranty(self):
        await self.send("buy G1", "720")
        receipt = self.signer.call_args.args[0]
        self.assertEqual((receipt["price"], receipt["claim"], receipt["warranty_hours"]), (720, "C", 0))

    async def test_near_expiry_is_a_fixed_price_at_cost(self):
        self.inventory({**self.food, "expires_at": time.time() + SHELF * 3600 * .05})
        await self.send("buy G1")
        self.assertEqual(self.last_ask(), COST)
        self.assertIn("Clearance reason: food_expiry_clearance", str(self.updater.requires_input.await_args.args[0]))
        await self.send(str(COST))
        self.assertEqual(self.signer.call_args.args[0]["price"], COST)

    async def test_falling_into_clearance_while_negotiating_discloses_again(self):
        await self.send("buy G1")
        self.inventory({**self.food, "expires_at": time.time() + SHELF * 3600 * .05})
        await self.send("500")
        self.signer.assert_not_called()
        self.assertEqual(self.last_ask(), COST)

    async def test_expired_is_refused_after_opening(self):
        await self.send("buy G1")
        self.inventory({**self.food, "expires_at": time.time() - 1})
        await self.send("720")
        self.updater.reject.assert_awaited_once()
        self.signer.assert_not_called()


class SellerGate(unittest.TestCase):
    SETTINGS = {"enabled": True, "trial_id": "v2", "categories": {KIMCHI: 2}, "max_total_units": 4, "max_total_estimated_cost": 150000}
    CATALOG = {"k": {"kind": "food", "category": KIMCHI, "list_price": 20000, "shelf_hours": 168}}
    INTEL = {"k": {"settles_6h": 9, "distinct_buyers": 5}}

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.md = Path(directory.name)

    def consumer(self, features):
        (self.md / "strategy-consumer.json").write_text(json.dumps({"protocol": 1, "pid": 1, "heartbeat_at": 1, "features": features}), encoding="utf-8")

    def run_for(self, inventory=None):
        return ft.for_run(self.md, inventory or {"units": []}, self.CATALOG, self.INTEL, 1000.0, settings=self.SETTINGS)

    def test_the_new_seller_registers_the_feature(self):
        self.assertIn(ft.REQUIRED_FEATURE, seller_r3.FEATURES)

    def test_a_seller_without_the_feature_gets_no_food(self):
        for setup in (lambda: None, lambda: self.consumer(["coded_seller_rules"]), lambda: self.consumer(None),
                      lambda: (self.md / "strategy-consumer.json").write_text("{broken", encoding="utf-8")):
            setup()
            with self.subTest(content=(self.md / "strategy-consumer.json").exists()):
                trial, _, picks, waiting = self.run_for()
                self.assertEqual((trial, picks, waiting), ("v2", [], "food_pricing_v2"))

    def test_the_restarted_seller_unlocks_the_trial(self):
        self.consumer(["coded_seller_rules", "food_pricing_v2"])
        _, _, picks, waiting = self.run_for()
        self.assertEqual([p["sku"] for p in picks], ["k"])
        self.assertIsNone(waiting)

    def test_a_trial_off_never_reports_waiting(self):
        self.assertEqual(ft.for_run(self.md, {"units": []}, self.CATALOG, self.INTEL, 1000.0, settings={"enabled": False}), (None, None, [], None))

    def test_the_first_trial_stays_and_does_not_count_against_this_one(self):
        first = {"planned": {"k": {"category": KIMCHI, "estimated_cost": 149999}, "a": {}, "b": {}, "c": {}}, "units": {"u": {"sku": "k"}}}
        (self.md / "food-trial.json").write_text(json.dumps(first), encoding="utf-8")
        self.consumer(["food_pricing_v2"])
        _, state, picks, _ = self.run_for()
        self.assertEqual([p["sku"] for p in picks], ["k"])
        ft.record_plan(state, picks, {"k"}, 1000.0)
        self.assertTrue(ft.save_state(self.md, state, "v2"))
        self.assertEqual(json.loads((self.md / "food-trial.json").read_text(encoding="utf-8")), first)
        self.assertEqual(sorted(json.loads((self.md / "food-trial-v2.json").read_text(encoding="utf-8"))["planned"]), ["k"])

    def test_no_second_buy_before_judging(self):
        self.consumer(["food_pricing_v2"])
        _, state, picks, _ = self.run_for()
        ft.record_plan(state, picks, {"k"}, 1000.0)
        ft.save_state(self.md, state, "v2")
        self.assertEqual(self.run_for()[2], [])

    def test_arrivals_are_kept_while_waiting(self):
        (self.md / "food-trial-v2.json").write_text(json.dumps({"planned": {"k": {}}, "units": {}}), encoding="utf-8")
        unit = {"unit": "u9", "sku": "k", "kind": "food", "cost": 1}
        _, state, picks, _ = self.run_for({"units": [unit]})
        self.assertIn("u9", state["units"])
        self.assertEqual(picks, [])

    def test_a_damaged_state_buys_nothing(self):
        self.consumer(["food_pricing_v2"])
        (self.md / "food-trial-v2.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_for()[1:3], (None, []))

    def test_settings_need_a_trial_id(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "food_trial.json"
            for bad in ({k: v for k, v in self.SETTINGS.items() if k != "trial_id"}, {**self.SETTINGS, "trial_id": "../x"}, {**self.SETTINGS, "trial_id": ""}, {**self.SETTINGS, "trial_id": 2}):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertEqual(ft.load_settings(path), {"enabled": False})

    def test_shipped_file_names_a_new_trial(self):
        self.assertEqual(ft.load_settings()["trial_id"], "v2")


if __name__ == "__main__":
    unittest.main()
