"""The counters' surplus check at the discount floor: the logged `required` is rounded to 6 places, sometimes up, and a counter at
the floor ceiling was then refused by less than 1e-6. The check now uses the exact need rounded down to 6 places; the counter's
offer stays at the floor ceiling, so no paid price is ever less discounted than the exact required discount."""

from fractions import Fraction
import json
import random
import unittest

import buyer
import discount_floor as floor
import test_discount_floor
from test_discount_floor import FakeClient


def setUpModule():
    test_discount_floor.setUpModule()


def tearDownModule():
    test_discount_floor.tearDownModule()

FLOOR_30 = {"floor": .3, "rates": floor.DEFAULT_RATES}
REPEAT = {"enabled": True, "max_ask_per_cap": 1.5, "max_rule_counters": 3, "min_surplus_per_list": .1}


def limit(list_price, settings=FLOOR_30, covered=False):
    return {"list_price": list_price, "kind": "goods", "covered": covered, "grades": ["A", "B"], "cap_without_floor": None, "settings": settings}


def client(text, list_price, budget, settings=FLOOR_30, covered=False):
    c = FakeClient(text, budget, limit(list_price, settings, covered))
    c.counter_repeat = dict(REPEAT)
    c.rule_counters = 0
    return c


class RequiredDown(unittest.TestCase):
    def test_logged_and_rounded_down(self):
        for grade in (None, "A", "B"):
            for covered in (False, True):
                need = floor.required("goods", grade, covered, FLOOR_30, ["A", "B"])
                _, check = floor.deal_limit(limit(1094400, covered=covered), grade, 10 ** 9)
                down = Fraction(str(check["required_down"]))
                self.assertLessEqual(down, need)
                self.assertLess(need - down, Fraction(1, 10 ** 6))
                self.assertEqual(check["required"], round(float(need), 6))
                json.dumps(check)

    def test_the_live_case_rounds_up_in_the_log(self):
        # (.3 + .098) / .902 = .4412416..., logged as .441242: above the exact need.
        _, check = floor.deal_limit(limit(1094400), None, 726600)
        self.assertGreater(Fraction(str(check["required"])), floor.required("goods", None, False, FLOOR_30, ["A", "B"]))
        self.assertEqual(check["ceiling"], 611505)


class Counters(unittest.TestCase):
    def test_the_repeat_counter_at_the_ceiling_is_sent(self):
        # The 05:xx live case: first counter over 1.18, repeat refused by the rounding; now the repeat goes at the ceiling.
        c = client("Grade unknown here. Asking price: 735396", 1094400, 726600)
        decision = c.decide()
        self.assertEqual((decision["action"], decision["price"]), ("offer", 611505))
        self.assertEqual(c.decisions[-1]["reason_code"], "rule_counter_repeat")

    def test_the_first_counter_at_the_ceiling_is_sent(self):
        c = client("Asking price: 640000", 1094400, 726600)
        decision = c.decide()
        self.assertEqual((decision["action"], decision["price"]), ("offer", 611505))
        self.assertEqual(c.decisions[-1]["reason_code"], "rule_counter_offer")

    def test_no_counter_is_ever_less_discounted_than_the_exact_need(self):
        rng = random.Random(7)
        sent = 0
        for _ in range(1500):
            list_price = rng.randint(1000, 3_000_000)
            settings = rng.choice((FLOOR_30, {"floor": .25, "rates": floor.DEFAULT_RATES}))
            covered = rng.random() < .3
            grade_text = rng.choice(("", "Grade A. ", "Grade B. "))
            grade = {"": None, "Grade A. ": "A", "Grade B. ": "B"}[grade_text]
            need = floor.required("goods", grade, covered, settings, ["A", "B"])
            top = floor.ceiling(list_price, need)
            if top < 2:
                continue
            ask = top + rng.randint(1, max(1, top // 3))
            c = client(f"{grade_text}Asking price: {ask}", list_price, rng.randint(top, top * 2), settings, covered)
            decision = c.decide()
            if decision["action"] == "offer":
                sent += 1
                self.assertLessEqual(decision["price"], top)
                self.assertGreaterEqual(Fraction(list_price - decision["price"], list_price), need)
        self.assertGreater(sent, 500)

    def test_the_counters_own_margin_still_holds(self):
        # A floor needing less than the counter's own .10 of list: the .10 still applies (cap 9500 on list 10000 leaves .05).
        c = client("Asking price: 9600", 10000, 9500, {"floor": .01, "rates": floor.DEFAULT_RATES})
        c.discount_limit = dict(c.discount_limit, kind="food")
        decision = c.decide()
        self.assertNotEqual(decision.get("price"), 9500)


if __name__ == "__main__":
    unittest.main()
