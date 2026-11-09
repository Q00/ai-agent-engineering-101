"""Goods-order first message A/B test (buyer_support.json goods_first_message_grades): settings, arm, wording, wiring."""

import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import first_message_ab as ab
import market_buyer as market
from test_market_buyer import TradeFixture, shop

ON = {"enabled": True, "share": 0.5, "salt": "gm1"}
TODAY_6H = "I want to buy lamp. Please include a market certificate. I require 6 hours of warranty in the checkout. "
TODAY_NONE = "I want to buy lamp. Please include a market certificate. If available, please offer a 6-hour goods warranty; no warranty is assumed. "


def settings_file(section, raw=None):
    folder = Path(tempfile.mkdtemp())
    path = folder / "buyer_support.json"
    path.write_text(raw if raw is not None else json.dumps({"goods_first_message_grades": section}), encoding="utf-8")
    return path


class Settings(unittest.TestCase):
    def test_shipped_section_is_well_formed(self):
        section = json.loads(ab.CONFIG.read_text(encoding="utf-8"))["goods_first_message_grades"]
        self.assertIs(type(section["enabled"]), bool)
        self.assertEqual((section["share"], section["salt"]), (0.8, "gm1"))
        self.assertEqual(ab.load_goods()["enabled"], section["enabled"])

    def test_valid_section_loads(self):
        self.assertEqual(ab.load_goods(settings_file(ON)), ON)

    def test_missing_or_malformed_means_off(self):
        for section in (None, {}, {**ON, "enabled": "true"}, {**ON, "share": 0}, {**ON, "share": 1}, {**ON, "share": "0.5"},
                        {**ON, "share": float("nan")}, {**ON, "salt": ""}, {**ON, "salt": None}, [ON]):
            with self.subTest(section=section):
                self.assertEqual(ab.load_goods(settings_file(section)), ab.DISABLED)
        self.assertEqual(ab.load_goods(settings_file(None, raw="{broken")), ab.DISABLED)

    def test_food_and_goods_sections_are_separate(self):
        path = Path(tempfile.mkdtemp()) / "buyer_support.json"
        path.write_text(json.dumps({"first_message_conditions": {"enabled": True, "share": 0.8, "salt": "fm1"}}), encoding="utf-8")
        self.assertEqual(ab.load_goods(path), ab.DISABLED)
        self.assertEqual(ab.load(path)["share"], 0.8)


class Arm(unittest.TestCase):
    def test_split_is_even_and_independent_of_the_food_salt(self):
        ids = [f"o-{i:012x}" for i in range(4000)]
        goods = [ab.arm(o, ON) for o in ids]
        self.assertTrue(0.46 < goods.count("conditions") / len(goods) < 0.54)
        food = [ab.arm(o, {"enabled": True, "share": 0.5, "salt": "fm1"}) for o in ids]
        self.assertNotEqual(goods, food)


class Wording(unittest.TestCase):
    def test_grade_sentence(self):
        self.assertEqual(ab.goods_grade_sentence({"cert": True, "grades": ["A", "B"], "warranty_hours": 6}), "This order accepts grade A or B. ")
        self.assertEqual(ab.goods_grade_sentence({"grades": ["A"]}), "This order accepts grade A. ")
        self.assertEqual(ab.goods_grade_sentence({"grades": ["A", "B", "C"]}), "This order accepts grade A, B or C. ")

    def test_no_grades_means_no_sentence(self):
        for bad in ({}, None, {"grades": []}, {"grades": ["X"]}, {"grades": "AB"}):
            with self.subTest(bad=bad):
                self.assertIsNone(ab.goods_grade_sentence(bad))

    def test_facts_only(self):
        text = ab.goods_grade_sentence({"grades": ["A", "B"]}).lower()
        for word in ("must", "require", "only", "immediately", "!", "review", "star", "promise", "ignore", "instruction"):
            self.assertNotIn(word, text)


class Sent(TradeFixture):
    def first_text(self):
        return next(p["params"]["message"]["parts"][0]["text"] for p in self.posts if p["method"] == "SendMessage")

    def run_trade(self, **options):
        return market.trade(shop("fixture"), "lamp", 300, transport=self.transport("asking 110."), fast_rules=True,
                            model_gate=lambda: "reserved", max_turns=3, **options)

    def test_control_is_byte_for_byte_today(self):
        self.run_trade(requirements={"cert": True, "grades": ["A", "B"], "warranty_hours": 6})
        self.assertEqual(self.first_text(), TODAY_6H)
        self.posts.clear()
        self.run_trade(requirements={"cert": True, "grades": ["A", "B"]}, goods_grade_sentence=None)
        self.assertEqual(self.first_text(), TODAY_NONE)

    def test_conditions_inserts_only_the_grade_sentence(self):
        _, record = self.run_trade(requirements={"cert": True, "grades": ["A", "B"], "warranty_hours": 6},
                                   goods_grade_sentence="This order accepts grade A or B. ")
        self.assertEqual(self.first_text(), "I want to buy lamp. This order accepts grade A or B. Please include a market certificate. "
                                            "I require 6 hours of warranty in the checkout. ")
        self.assertEqual(record["outcome"], "purchased")
        self.assertEqual(record["price"], 110)
        self.posts.clear()
        self.run_trade(requirements={"cert": True, "grades": ["A", "B"]}, goods_grade_sentence="This order accepts grade A or B. ")
        self.assertEqual(self.first_text(), TODAY_NONE.replace("lamp. ", "lamp. This order accepts grade A or B. ", 1))

    def test_food_message_ignores_the_goods_sentence(self):
        self.run_trade(goods_grade_sentence="This order accepts grade A or B. ")
        self.assertEqual(self.first_text(), "I want to buy a lamp")


class Wiring(unittest.TestCase):
    """trade_attempt: only goods orders get a goods arm; food keeps its own test."""

    def attempt(self, order, goods_settings, food_settings=ab.DISABLED, accept=None):
        folder = Path(tempfile.mkdtemp())
        (folder / "order.json").write_text("{}", encoding="utf-8")
        args = types.SimpleNamespace(mode="claude", order=folder / "order.json", budget=1000, memory=folder / "memory.jsonl", item="x")
        if accept is None:
            accept = {"cert": True, "grades": ["A", "B"]} | ({"min_hours_left": 74.0} if order["kind"] == "food" else {"warranty_hours": 6})
        pick = {"shop": {"name": "s", "url": "http://s.test", "offers": []}, "item": "G1", "budget": 900, "accept": accept,
                "selected": {}, "decision": {"max_turns": 3}, "reason": "fixture"}
        seen = {}
        def fake_trade(shop_, item, budget, **options):
            seen.update(options)
            return {"shop": shop_["url"], "receipt": None, "stars": 3, "review": ""}, {"outcome": "no_deal"}
        with patch.object(market, "trade", fake_trade), patch.object(ab, "load", lambda path=None: food_settings), \
             patch.object(ab, "load_goods", lambda path=None: goods_settings), patch.object(market, "remember", lambda *a, **k: None):
            _, record = market.trade_attempt(args, order, {}, pick)
        return seen, record

    def order_in(self, wanted):
        return next(f"o-{i:012x}" for i in range(1000) if ab.arm(f"o-{i:012x}", ON) == wanted)

    def test_goods_conditions_arm(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "goods"}, ON)
        self.assertEqual(seen["goods_grade_sentence"], "This order accepts grade A or B. ")
        self.assertEqual(seen["requirements"], {"cert": True, "grades": ["A", "B"], "warranty_hours": 6})
        self.assertNotIn("first_message", seen)
        self.assertEqual(record["goods_message_arm"], "conditions")
        self.assertIsNone(record["first_message_arm"])

    def test_goods_control_arm(self):
        seen, record = self.attempt({"order": self.order_in("control"), "kind": "goods"}, ON)
        self.assertNotIn("goods_grade_sentence", seen)
        self.assertEqual(record["goods_message_arm"], "control")

    def test_goods_without_grades_sends_today(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "goods"}, ON, accept={"cert": True, "warranty_hours": 6})
        self.assertNotIn("goods_grade_sentence", seen)
        self.assertEqual(record["goods_message_arm"], "conditions")

    def test_food_gets_no_goods_arm(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "food"}, ON)
        self.assertNotIn("goods_grade_sentence", seen)
        self.assertIsNone(record["goods_message_arm"])

    def test_off_is_today(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "goods"}, ab.DISABLED)
        self.assertNotIn("goods_grade_sentence", seen)
        self.assertIsNone(record["goods_message_arm"])


if __name__ == "__main__":
    unittest.main()
