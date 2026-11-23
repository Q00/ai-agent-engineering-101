"""11/17 changes: endgame ask cap (seller), shelf verification before signing (buyer), flags in tuning.BOUNDS."""
import json
import re
import tempfile
import unittest
from pathlib import Path

import r3_buyer
import seller_r3 as s
import tuning

UNIT = {"cost": 30000, "list_price": 50000, "kind": "goods", "supplied_at": 0}


class EndgameAskTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(s.PRICING)

    def tearDown(self):
        s.PRICING.clear(); s.PRICING.update(self.saved)

    def test_flag_off_keeps_list_ask(self):
        s.PRICING.update(endgame_ask_mult=0.0, ask_of_list=0.86, endgame_floor=1.0)
        now = s.ROUND_END - 3 * 3600
        self.assertEqual(s.opening_ask(UNIT, now), 43000)

    def test_flag_on_caps_ask_near_floor_in_endgame(self):
        s.PRICING.update(endgame_ask_mult=1.15, ask_of_list=0.86, endgame_floor=0.97)
        now = s.ROUND_END - 3 * 3600
        floor = s.floor_price(UNIT, now)
        self.assertEqual(floor, 29100)
        self.assertEqual(s.opening_ask(UNIT, now), 33465)

    def test_flag_on_does_nothing_before_endgame(self):
        s.PRICING.update(endgame_ask_mult=1.15, ask_of_list=0.86, endgame_h=24)
        now = s.ROUND_END - 48 * 3600
        self.assertEqual(s.opening_ask(UNIT, now), 43000)


class ShelfVerifyTests(unittest.TestCase):
    def test_ask_text_for_food_has_no_digits(self):
        t = r3_buyer.ask_text({"name": "사과 10kg", "sku": "Gabc"}, food=True)
        self.assertIn("shelf life", t)
        self.assertIsNone(re.search(r"(?<![\w.])\d+(?![\w.])", t.replace("10kg", "")))

    def test_strict_walks_without_stated_hours(self):
        p = r3_buyer.ValueLadderPolicy(20000, min_hours=36.5, strict=True)
        p.last_text = "Our price is 15000."
        self.assertEqual(p.respond("x", 20000, 15000, 0).action, "walk")

    def test_strict_trades_when_hours_are_enough(self):
        p = r3_buyer.ValueLadderPolicy(20000, min_hours=36.5, strict=True)
        p.last_text = "사과 10kg, 48.0 h shelf life left. Price 15000"
        self.assertEqual(p.respond("x", 20000, 15000, 0).action, "offer")

    def test_short_hours_walk_even_when_not_strict(self):
        p = r3_buyer.ValueLadderPolicy(20000, min_hours=36.5)
        p.last_text = "fresh, 12 hours left. 15000"
        self.assertEqual(p.respond("x", 20000, 15000, 0).action, "walk")

    def test_shelf_refusals_counts_one_shop(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "payments.jsonl"
            rows = [{"shop": "https://a/", "paid": False, "reason": "not paying: 3 h of shelf life left"},
                    {"shop": "https://a", "paid": True}, {"shop": "https://b", "paid": False, "reason": "no certificate"}]
            f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            self.assertEqual(r3_buyer.shelf_refusals("https://a", f), 1)
            self.assertEqual(r3_buyer.shelf_refusals("https://b", f), 0)


class FlagBoundsTests(unittest.TestCase):
    def test_flags_are_clamped(self):
        self.assertEqual(tuning.clamp("seller", "endgame_ask_mult", 9), 2.0)
        self.assertEqual(tuning.clamp("buyer", "shelf_verify", 0.7), 1)


if __name__ == "__main__":
    unittest.main()
