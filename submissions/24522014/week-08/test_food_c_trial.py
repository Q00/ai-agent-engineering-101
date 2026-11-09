"""Buyer option 3, grade C food (r3_policy buying.c_trial_*): food orders in the trial arm (own order hash, salt fc1) also accept
grade C. A C quote, or a quote with no grade once C is allowed, needs the floor with a food-C penalty p: (floor + p) / (1 - p),
p = max(3 x the food-C broken rate seen in the ledger samples (0 of 12,131), .05) = .05. The shelf-life rule, the certificate,
goods orders and the control arm are unchanged. Off unless c_trial_enabled is true and the trial fields are valid."""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discount_floor as floor
import first_message_ab
import food_c_trial
import market_buyer as market
import standing_ask
from test_market_buyer import TradeFixture, shop
from test_standing_ask import Seller, COUNTER, REPEAT

ON = {"c_trial_enabled": True, "c_trial_share": 0.5, "c_trial_salt": "fc1", "c_trial_food_penalty": 0.05}
BASE = {"floor": 0.3, "rates": floor.DEFAULT_RATES}
WITH_C = {**BASE, "food_c_penalty": 0.05}


class Settings(unittest.TestCase):
    def load(self, buying):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps({"buying": buying}), encoding="utf-8")
            return food_c_trial.load(path)

    def test_off_unless_enabled_and_valid(self):
        self.assertTrue(self.load(ON)["enabled"])
        for bad in ({**ON, "c_trial_enabled": False}, {**ON, "c_trial_enabled": "true"}, {**ON, "c_trial_share": 1.0}, {**ON, "c_trial_salt": ""},
                    {**ON, "c_trial_food_penalty": 0.04}, {**ON, "c_trial_food_penalty": 0.5}, {k: v for k, v in ON.items() if k != "c_trial_food_penalty"}):
            with self.subTest(bad=bad):
                self.assertFalse(self.load(bad)["enabled"])
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(food_c_trial.load(Path(folder) / "missing.json")["enabled"])

    def test_the_shipped_setting(self):
        buying = json.loads(food_c_trial.CONFIG.read_text(encoding="utf-8"))["buying"]
        self.assertEqual((buying["c_trial_share"], buying["c_trial_salt"], buying["c_trial_food_penalty"]), (0.5, "fc1", 0.05))
        self.assertEqual(buying["grades"], ["A", "B"])
        self.assertIs(buying["c_trial_enabled"], True)

    def test_its_hash_is_independent_of_the_standing_ask_hash(self):
        mine = {"enabled": True, "share": .5, "salt": "fc1"}
        other = {"enabled": True, "share": .5, "salt": "sa1"}
        cells = {}
        for i in range(8000):
            key = (food_c_trial.arm(f"o-{i:012x}", {**mine, "penalty": .05}), standing_ask.arm(f"o-{i:012x}", other))
            cells[key] = cells.get(key, 0) + 1
        self.assertEqual(len(cells), 4)
        for n in cells.values():
            self.assertAlmostEqual(n / 8000, .25, delta=.02)


class Floor(unittest.TestCase):
    def test_food_c_needs_the_penalised_floor(self):
        self.assertEqual(floor.required("food", "C", False, WITH_C, ["A", "B", "C"]), (Fraction("0.3") + Fraction("0.05")) / (1 - Fraction("0.05")))
        self.assertEqual(floor.ceiling(100000, floor.required("food", "C", False, WITH_C, ["A", "B", "C"])), 63157)

    def test_a_food_quote_with_no_grade_counts_as_c_once_c_is_allowed(self):
        self.assertEqual(floor.required("food", None, False, WITH_C, ["A", "B", "C"]), floor.required("food", "C", False, WITH_C, ["A", "B", "C"]))
        self.assertEqual(floor.required("food", None, False, WITH_C, ["A", "B"]), Fraction("0.3"))

    def test_food_a_and_b_and_every_goods_case_are_unchanged(self):
        for grade in ("A", "B"):
            self.assertEqual(floor.required("food", grade, False, WITH_C, ["A", "B", "C"]), Fraction("0.3"))
        for args in (("goods", "A", False), ("goods", "B", True), ("goods", "C", False), ("goods", None, False)):
            self.assertEqual(floor.required(*args, WITH_C, ["A", "B"]), floor.required(*args, BASE, ["A", "B"]))
        # Without the trial setting nothing changes, C or not.
        self.assertEqual(floor.required("food", "C", False, BASE, ["A", "B", "C"]), Fraction("0.3"))

    def test_the_expected_value_matches_the_floor(self):
        need = float(floor.required("food", "C", False, WITH_C, ["A", "B", "C"]))
        self.assertAlmostEqual(floor.expected_discount(need, "food", "C", False, WITH_C, ["A", "B", "C"]), 0.3, places=9)
        self.assertEqual(floor.expected_discount(.4, "food", "B", False, WITH_C, ["A", "B", "C"]), .4)


class Wiring(unittest.TestCase):
    def pick(self, kind, arm):
        order = {"order": "o-1", "kind": kind, "use_within_hours": 10} if kind == "food" else {"order": "o-1", "kind": kind}
        with patch.object(food_c_trial, "arm", return_value=arm), patch.object(market, "prepare_support",
                                                                               return_value=(shop("s"), "G1", 60000, "r", [], {"status": "reserved", "max_turns": 3, "fast_rules": True})):
            return market.select_attempt(Path("."), [], [], {}, 90000, {}, order)

    def test_the_trial_arm_adds_c_for_food_only(self):
        self.assertEqual(self.pick("food", "grade_c")["accept"]["grades"], ["A", "B", "C"])
        self.assertEqual(self.pick("food", "grade_c")["food_c_arm"], "grade_c")
        self.assertEqual(self.pick("food", "control")["accept"]["grades"], ["A", "B"])
        self.assertEqual(self.pick("food", None)["accept"]["grades"], ["A", "B"])
        goods = self.pick("goods", "grade_c")
        self.assertEqual(goods["accept"]["grades"], ["A", "B"])
        self.assertIsNone(goods["food_c_arm"])

    def test_the_shelf_life_and_certificate_rules_are_unchanged(self):
        a, b = self.pick("food", "grade_c")["accept"], self.pick("food", "control")["accept"]
        self.assertEqual({k: v for k, v in a.items() if k != "grades"}, {k: v for k, v in b.items() if k != "grades"})

    def test_trade_attempt_passes_the_penalty_and_the_kit_gets_c(self):
        seen = {}
        def trade(shop_, item, budget, **options):
            seen.update(options)
            return {"shop": shop_["url"], "receipt": None, "stars": 3, "review": "r"}, {"outcome": "no_deal", "decision_trail": []}
        with tempfile.TemporaryDirectory() as folder:
            md = Path(folder) / ".market"
            md.mkdir()
            args = argparse.Namespace(mode="codex", order=md / "order.json", budget=90000, item="x", memory=Path(folder) / "m.jsonl")
            trial_on = {"enabled": True, "share": .5, "salt": "fc1", "penalty": .05}
            for arm, grades, trial, expect in (("grade_c", ["A", "B", "C"], trial_on, ["A", "B", "C"]), ("control", ["A", "B"], trial_on, ["A", "B"]),
                                               ("grade_c", ["A", "B", "C"], dict(food_c_trial.OFF), ["A", "B"])):
                with self.subTest(arm=arm, trial=trial["enabled"]):
                    seen.clear()
                    pick = {"shop": shop("s"), "item": "G1", "accept": {"cert": True, "grades": list(grades), "min_hours_left": 12}, "selected": {"cap_without_floor": 60000},
                            "decision": {"max_turns": 3, "fast_rules": True}, "reason": "r", "budget": 60000, "food_c_arm": arm}
                    grades = expect
                    with patch.object(market, "trade", side_effect=trade), patch.object(market, "remember"), patch.object(food_c_trial, "load", return_value=trial):
                        result, record = market.trade_attempt(args, {"order": "o-1", "kind": "food", "item": "x"}, {"G1": {"kind": "food", "list_price": 100000}}, pick)
                    self.assertEqual(result["accept"]["grades"], grades)
                    self.assertEqual(record["food_c_arm"], arm)
                    if arm == "grade_c" and trial["enabled"]:
                        self.assertEqual(seen["discount_limit"]["settings"]["food_c_penalty"], 0.05)
                        self.assertEqual(seen["discount_limit"]["grades"], ["A", "B", "C"])
                    elif arm == "grade_c":
                        # Turned off between selection and trade: C is dropped again, since without its penalty C is not safe.
                        self.assertNotIn("food_c_penalty", seen["discount_limit"]["settings"])
                        self.assertEqual(seen["discount_limit"]["grades"], ["A", "B"])
                    else:
                        self.assertNotIn("food_c_penalty", seen["discount_limit"]["settings"])


class Trading(TradeFixture):
    """End to end on the rule path: a C quote is priced against the penalised floor; the control arm refuses it as today."""

    def trade_food(self, seller, grades, penalty):
        settings = {**BASE, **({"food_c_penalty": penalty} if penalty else {})}
        limit = {"list_price": 100000, "kind": "food", "covered": False, "grades": grades, "cap_without_floor": 70000, "settings": settings}
        accept = {"cert": True, "grades": grades}
        with patch("buyer.run_harness"):
            return market.trade(shop("fixture"), "lamp", 70000, transport=seller.transport(), fast_rules=True, model_gate=lambda: False, max_turns=3,
                                over_budget_counter=COUNTER, counter_repeat=REPEAT, catalog_list_price=100000, discount_limit=limit, accept_conditions=accept)

    def test_a_c_quote_at_or_under_the_penalised_ceiling_is_bought(self):
        seller = Seller(63157, texts=["Grade C, certificate included. Asking price 63157."] * 4)
        result, record = self.trade_food(seller, ["A", "B", "C"], .05)
        self.assertEqual(result["receipt"]["price"], 63157)

    def test_a_c_quote_above_it_is_not(self):
        seller = Seller(65000, texts=["Grade C, certificate included. Asking price 65000."] * 4)
        result, record = self.trade_food(seller, ["A", "B", "C"], .05)
        self.assertIsNone(result["receipt"])
        self.assertTrue(all(o <= 63157 for o in seller.offers))

    def test_the_control_arm_refuses_c_as_today(self):
        seller = Seller(50000, texts=["Grade C, certificate included. Asking price 50000."] * 4)
        result, record = self.trade_food(seller, ["A", "B"], None)
        self.assertIsNone(result["receipt"])
        self.assertEqual(seller.offers, [])
        self.assertEqual(record["decision_trail"][0]["reason_code"], "quote_grade_unacceptable")


if __name__ == "__main__":
    unittest.main()
