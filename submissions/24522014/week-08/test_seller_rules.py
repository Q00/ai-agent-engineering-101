"""Coded seller moves never sell below the floor and follow the logged model's shape."""

import itertools
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import seller_rules as sr


class Moves(unittest.TestCase):
    def test_opening_is_the_quote(self):
        self.assertEqual(sr.rule_move(None, None, 80, 100, 0)[0], {"action": "counter", "price": 100})

    def test_offers_below_the_floor_are_never_accepted(self):
        for offer in (1, 50, 79):
            with self.subTest(offer=offer):
                self.assertEqual(sr.rule_move(100, offer, 80, 100, 1)[0]["action"], "counter")

    def test_concession_follows_the_logged_shape_toward_the_floor(self):
        self.assertEqual(sr.rule_move(100, 50, 80, 100, 1)[0]["price"], 95)
        self.assertEqual(sr.rule_move(100, 50, 80, 100, 2)[0]["price"], 94)
        self.assertEqual(sr.rule_move(100, 50, 80, 100, 3)[0]["price"], 91)
        self.assertEqual(sr.rule_move(100, 50, 80, 100, 9)[0]["price"], 91)

    def test_concession_aims_at_the_offer_when_it_clears_the_floor(self):
        self.assertEqual(sr.rule_move(100, 90, 80, 100, 3)[0], {"action": "counter", "price": 96})

    def test_accepts_only_above_the_floor_and_close_to_the_ask(self):
        self.assertEqual(sr.rule_move(100, 98, 80, 100, 1)[0], {"action": "accept", "price": 98})
        self.assertEqual(sr.rule_move(100, 97, 80, 100, 1)[0]["action"], "counter")

    def test_never_reaches_below_the_floor_with_a_real_gap(self):
        self.assertEqual(sr.rule_move(81, 10, 80, 100, 9)[0]["price"], 80)
        self.assertEqual(sr.rule_move(80, 10, 80, 100, 9)[0]["price"], 80)

    def test_a_small_gap_still_moves(self):
        self.assertEqual(sr.rule_move(1000, 1, 999, 1000, 1)[0]["price"], 999)

    def test_offer_at_or_above_the_ask_holds_the_ask(self):
        self.assertEqual(sr.rule_move(100, 120, 80, 100, 1)[0], {"action": "counter", "price": 100})

    def test_floor_above_a_stale_ask_never_accepts_a_low_offer(self):
        move = sr.rule_move(100, 90, 120, 130, 1)[0]
        self.assertEqual(move, {"action": "counter", "price": 120})

    def test_exhaustive_safety(self):
        for ask, offer, floor, asks in itertools.product(range(50, 130, 7), range(1, 150, 3), range(40, 125, 5), range(1, 6)):
            if ask < floor:
                continue
            move, explanation = sr.rule_move(ask, offer, floor, 130, asks)
            self.assertTrue(explanation)
            if move["action"] == "accept":
                self.assertGreaterEqual(move["price"], floor)
                self.assertEqual(move["price"], offer)
                self.assertLess(offer, ask)
            else:
                self.assertEqual(move["action"], "counter")
                self.assertGreaterEqual(move["price"], floor)
                self.assertLessEqual(move["price"], ask)
                self.assertIs(type(move["price"]), int)
                if offer < ask:
                    self.assertGreater(move["price"], min(offer, ask - 1) if offer >= floor else floor - 1)


class Mode(unittest.TestCase):
    def test_only_an_explicit_model_value_uses_the_model(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seller_decision.json"
            self.assertEqual(sr.decision_mode(path), "rules")
            for content, mode in (('{"mode": "model"}', "model"), ('{"mode": "rules"}', "rules"), ('{"mode": "MODEL"}', "rules"),
                                  ("{broken", "rules"), ("[]", "rules"), ("{}", "rules")):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    self.assertEqual(sr.decision_mode(path), mode)


if __name__ == "__main__":
    unittest.main()
