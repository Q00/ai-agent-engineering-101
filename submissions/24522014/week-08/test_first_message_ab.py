"""Food-order first message A/B test (buyer_support.json first_message_conditions): settings, arm, wording, wiring."""

import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import buyer_fast
import first_message_ab as ab
import market_buyer as market
from test_market_buyer import TradeFixture, shop

ON = {"enabled": True, "share": 0.5, "salt": "fm1"}
FULL = "I want to buy a G1831f2c0. This order accepts grade A or B and needs at least 362 hours of shelf life left. Please include a market certificate."


def settings_file(section, raw=None):
    folder = Path(tempfile.mkdtemp())
    path = folder / "buyer_support.json"
    path.write_text(raw if raw is not None else json.dumps({"first_message_conditions": section}), encoding="utf-8")
    return path


class Settings(unittest.TestCase):
    def test_shipped_section_is_well_formed(self):
        section = json.loads(ab.CONFIG.read_text(encoding="utf-8"))["first_message_conditions"]
        self.assertIs(type(section["enabled"]), bool)
        # Concluded 11-09 at the 12 h check (paid +13.4 pts, z 4.21): .9999, the highest share the validator takes (0 < share < 1).
        self.assertEqual((section["share"], section["salt"]), (0.9999, "fm1"))
        loaded = ab.load()
        self.assertEqual(loaded["enabled"], section["enabled"])

    def test_valid_section_loads(self):
        self.assertEqual(ab.load(settings_file(ON)), ON)

    def test_missing_or_malformed_means_off(self):
        for section in (None, {}, {**ON, "enabled": "true"}, {**ON, "enabled": 1}, {**ON, "share": 0}, {**ON, "share": 1},
                        {**ON, "share": 1.5}, {**ON, "share": True}, {**ON, "share": "0.5"}, {**ON, "share": float("nan")},
                        {**ON, "salt": ""}, {**ON, "salt": 7}, {**ON, "salt": None}, [ON]):
            with self.subTest(section=section):
                self.assertEqual(ab.load(settings_file(section)), ab.DISABLED)
        self.assertEqual(ab.load(settings_file(None, raw="{broken")), ab.DISABLED)
        self.assertEqual(ab.load(Path(tempfile.mkdtemp()) / "absent.json"), ab.DISABLED)


class Arm(unittest.TestCase):
    def test_off_or_bad_order_gives_no_arm(self):
        self.assertIsNone(ab.arm("o-2c1ac7207abf", ab.DISABLED))
        for order in (None, "", 7):
            self.assertIsNone(ab.arm(order, ON))

    def test_arm_is_stable_and_follows_the_salt(self):
        arms = [ab.arm(f"o-{i:012x}", ON) for i in range(4000)]
        self.assertEqual(arms, [ab.arm(f"o-{i:012x}", ON) for i in range(4000)])
        share = arms.count("conditions") / len(arms)
        self.assertTrue(0.46 < share < 0.54, share)
        self.assertEqual(set(arms), {"conditions", "control"})
        other = [ab.arm(f"o-{i:012x}", {**ON, "salt": "fm2"}) for i in range(4000)]
        self.assertNotEqual(arms, other)

    def test_share_sets_the_split(self):
        arms = [ab.arm(f"o-{i:012x}", {**ON, "share": 0.2}) for i in range(4000)]
        self.assertTrue(0.17 < arms.count("conditions") / len(arms) < 0.23)

    def test_raising_the_share_keeps_every_conditions_order(self):
        ids = [f"o-{i:012x}" for i in range(4000)]
        before = [ab.arm(o, ON) for o in ids]
        after = [ab.arm(o, {**ON, "share": 0.8}) for o in ids]
        self.assertTrue(all(a == "conditions" for b, a in zip(before, after) if b == "conditions"))
        self.assertTrue(0.77 < after.count("conditions") / len(after) < 0.83)


class Wording(unittest.TestCase):
    def test_full_sentence_is_the_proposed_one(self):
        self.assertEqual(ab.text("G1831f2c0", {"cert": True, "grades": ["A", "B"], "min_hours_left": 362.0}), FULL)

    def test_hours_are_written_plainly(self):
        self.assertIn("at least 74 hours", ab.text("G1", {"grades": ["A", "B"], "min_hours_left": 74.0}))
        self.assertIn("at least 4.5 hours", ab.text("G1", {"grades": ["A", "B"], "min_hours_left": 4.5}))
        self.assertIn("at least 26 hours", ab.text("G1", {"grades": ["A", "B"], "min_hours_left": 26}))

    def test_grade_lists(self):
        self.assertIn("accepts grade A and", ab.text("G1", {"grades": ["A"], "min_hours_left": 5}))
        self.assertIn("accepts grade A, B or C and", ab.text("G1", {"grades": ["A", "B", "C"], "min_hours_left": 5}))

    def test_missing_values_drop_their_part(self):
        self.assertEqual(ab.text("G1", {"cert": True, "min_hours_left": 20}),
                         "I want to buy a G1. This order needs at least 20 hours of shelf life left. Please include a market certificate.")
        self.assertEqual(ab.text("G1", {"cert": True, "grades": ["A", "B"]}),
                         "I want to buy a G1. This order accepts grade A or B. Please include a market certificate.")
        self.assertEqual(ab.text("G1", {"grades": ["A", "B"], "min_hours_left": 20}),
                         "I want to buy a G1. This order accepts grade A or B and needs at least 20 hours of shelf life left.")
        for bad in ({"grades": [], "min_hours_left": None}, {"grades": ["X"], "min_hours_left": -1}, {"min_hours_left": float("inf")}, {}, None):
            with self.subTest(bad=bad):
                self.assertEqual(ab.text("G1", bad), "I want to buy a G1.")

    def test_facts_only(self):
        text = ab.text("G1831f2c0", {"cert": True, "grades": ["A", "B"], "min_hours_left": 362.0}).lower()
        for word in ("must", "require", "only", "immediately", "or else", "!", "review", "star", "promise", "ignore", "instruction"):
            self.assertNotIn(word, text)


class EchoedHours(unittest.TestCase):
    def test_hours_echoed_by_a_seller_are_not_read_as_the_ask(self):
        reading = buyer_fast.load_reading()
        for text, ask in (("Sprite (sku=G2f086429), asking price 14280. Remaining shelf life is 485.43h. You need at least 362 hours of shelf life left.", 14280),
                          ("This order needs at least 362 hours of shelf life left. Our unit has 400 hours left. asking 9030.", 9030),
                          ("You asked for at least 362 hours. Counteroffer: 9030.", 9030)):
            with self.subTest(text=text):
                self.assertEqual(buyer_fast.classify(text, 20000, None, None, reading)[2], ask)


class Sent(TradeFixture):
    def first_text(self):
        return next(p["params"]["message"]["parts"][0]["text"] for p in self.posts if p["method"] == "SendMessage")

    def run_trade(self, **options):
        return market.trade(shop("fixture"), "lamp", 300, transport=self.transport("asking 110."), fast_rules=True,
                            model_gate=lambda: "reserved", max_turns=3, **options)

    def test_control_is_byte_for_byte_today(self):
        self.run_trade()
        self.assertEqual(self.first_text(), "I want to buy a lamp")
        self.posts.clear()
        self.run_trade(first_message=None)
        self.assertEqual(self.first_text(), "I want to buy a lamp")

    def test_conditions_text_is_sent_and_the_trade_goes_on(self):
        text = ab.text("lamp", {"cert": True, "grades": ["A", "B"], "min_hours_left": 74.0})
        _, record = self.run_trade(first_message=text)
        self.assertEqual(self.first_text(), text)
        self.assertEqual(record["outcome"], "purchased")
        self.assertEqual(record["price"], 110)

    def test_goods_requirements_keep_their_wording(self):
        self.run_trade(requirements={"cert": True, "warranty_hours": 6}, first_message="ignored")
        self.assertTrue(self.first_text().startswith("I want to buy lamp. Please include a market certificate."))


class Wiring(unittest.TestCase):
    """trade_attempt: only food orders get an arm; only the conditions arm changes the first message."""

    def attempt(self, order, settings):
        folder = Path(tempfile.mkdtemp())
        (folder / "order.json").write_text("{}", encoding="utf-8")
        args = types.SimpleNamespace(mode="claude", order=folder / "order.json", budget=1000, memory=folder / "memory.jsonl", item="x")
        accept = {"cert": True, "grades": ["A", "B"]} | ({"min_hours_left": 74.0} if order["kind"] == "food" else {})
        pick = {"shop": {"name": "s", "url": "http://s.test", "offers": []}, "item": "G1", "budget": 900, "accept": accept,
                "selected": {}, "decision": {"max_turns": 3}, "reason": "fixture"}
        seen = {}
        def fake_trade(shop_, item, budget, **options):
            seen.update(options)
            return {"shop": shop_["url"], "receipt": None, "stars": 3, "review": ""}, {"outcome": "no_deal"}
        with patch.object(market, "trade", fake_trade), patch.object(ab, "load", lambda path=None: settings), \
             patch.object(market, "remember", lambda *a, **k: None):
            _, record = market.trade_attempt(args, order, {}, pick)
        return seen, record

    def order_in(self, wanted):
        return next(f"o-{i:012x}" for i in range(1000) if ab.arm(f"o-{i:012x}", ON) == wanted)

    def test_food_conditions_arm(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "food"}, ON)
        self.assertEqual(seen["first_message"], "I want to buy a G1. This order accepts grade A or B and needs at least 74 hours of shelf life left. Please include a market certificate.")
        self.assertNotIn("requirements", seen)
        self.assertEqual(record["first_message_arm"], "conditions")

    def test_food_control_arm(self):
        seen, record = self.attempt({"order": self.order_in("control"), "kind": "food"}, ON)
        self.assertNotIn("first_message", seen)
        self.assertEqual(record["first_message_arm"], "control")

    def test_goods_get_no_arm(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "goods"}, ON)
        self.assertNotIn("first_message", seen)
        self.assertEqual(seen["requirements"], {"cert": True, "grades": ["A", "B"]})
        self.assertIsNone(record["first_message_arm"])

    def test_off_is_today(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "food"}, ab.DISABLED)
        self.assertNotIn("first_message", seen)
        self.assertIsNone(record["first_message_arm"])


if __name__ == "__main__":
    unittest.main()
