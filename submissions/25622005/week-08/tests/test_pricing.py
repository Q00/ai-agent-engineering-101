"""tools/pricing.py: D3 floor, ask and concession, D4 below-cost, D5 warranty, D14 schedule, L5 claim and fact line."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-pricing-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import policy
from tools import pricing

H = 3600
END = 1794927540
NOW = END - 200 * H                      # far from the end of the round


def pol(**over):
    p = copy.deepcopy(policy.DEFAULTS)
    for k, v in over.items():
        sec, key = k.split("__")
        p[sec][key] = v
    return p


def goods(cost=50000, lst=100000, grade="A", age_h=1, now=NOW):
    return {"unit": "u-g", "sku": "G000f6cda", "name": "Curtain", "category": "a>b>커튼", "kind": "goods",
            "list_price": lst, "cost": cost, "grade": grade, "supplied_at": now - age_h * H, "expires_at": None,
            "cert": {"cert": {}, "sig": "x"}}


def food(hours_left, shelf=96, cost=10000, lst=20000, now=NOW):
    return {"unit": "u-f", "sku": "G0011e806", "name": "Milk", "category": "식품>우유/두유", "kind": "food",
            "list_price": lst, "cost": cost, "grade": "B", "supplied_at": now - H,
            "expires_at": now + hours_left * H, "shelf_hours": shelf, "cert": {"cert": {}, "sig": "x"}}


CTX = {"uwh": {"n": 0, "median": None}, "warranted_6h": 0}


class FloorAndAsk(unittest.TestCase):
    def test_a_grade_worst_cost(self):
        u = goods(cost=75000)                                    # A-grade cost can reach 0.75 x list
        self.assertEqual(pricing.floor(u, CTX, pol(), NOW), 81000)  # 75000 x 1.08
        self.assertEqual(pricing.first_ask(u, CTX, pol(), NOW), 85000)

    def test_cheap_unit_floor_is_list_share(self):
        u = goods(cost=40000)
        self.assertEqual(pricing.floor_detail(u, CTX, pol(), NOW), (60000, ["D3"]))
        self.assertEqual(pricing.first_ask(u, CTX, pol(), NOW), 85000)

    def test_ask_never_below_floor(self):
        u = goods(cost=90000)
        self.assertEqual(pricing.first_ask(u, CTX, pol(), NOW), pricing.floor(u, CTX, pol(), NOW))

    def test_intro_price_extension_point_is_off(self):
        self.assertIsNone(pricing.intro_ask(goods(), CTX, pol()))
        self.assertEqual(pricing.first_ask(goods(), CTX, pol(seller__intro_price_enabled=True), NOW), 85000)


class Respond(unittest.TestCase):
    def setUp(self):
        self.st = {"floor": 60000, "first_ask": 85000, "ask": 85000, "countered": False}   # midpoint 72500

    def test_paths(self):
        p = pol()
        self.assertEqual(pricing.respond(self.st, 90000, p), ("accept", 85000))    # never above our ask
        self.assertEqual(pricing.respond(self.st, 85000, p), ("accept", 85000))
        self.assertEqual(pricing.respond(self.st, 72500, p), ("accept", 72500))
        self.assertEqual(pricing.respond(self.st, 65000, p), ("counter", 72500))
        self.assertEqual(pricing.respond(self.st, 59999, p), ("final", 60000))
        self.assertEqual(pricing.respond(self.st, None, p), ("counter", 85000))

    def test_after_one_counter_accept_at_or_above_floor(self):
        st = dict(self.st, ask=72500, countered=True)
        self.assertEqual(pricing.respond(st, 61000, pol()), ("accept", 61000))
        self.assertEqual(pricing.respond(st, 59000, pol()), ("final", 60000))

    def test_after_final_floor_is_accepted(self):
        st = dict(self.st, ask=60000, final=True)
        self.assertEqual(pricing.respond(st, 60000, pol()), ("accept", 60000))


class BelowCost(unittest.TestCase):
    def test_food_with_observations(self):
        ctx = dict(CTX, uwh={"n": 10, "median": 24})
        self.assertEqual(pricing.floor_detail(food(40), ctx, pol(), NOW), (1, ["D3", "D4-food"]))   # 40 < 2 x 24
        self.assertEqual(pricing.floor_detail(food(60), ctx, pol(), NOW), (12000, ["D3"]))

    def test_food_without_enough_observations_uses_shelf_share(self):
        ctx = dict(CTX, uwh={"n": 9, "median": 24})
        self.assertEqual(pricing.floor(food(19, shelf=96), ctx, pol(), NOW), 1)        # 19/96 < 0.25
        self.assertEqual(pricing.floor(food(40, shelf=96), ctx, pol(), NOW), 12000)

    def test_food_clears_only_near_expiry(self):
        ctx = dict(CTX, uwh={"n": 10, "median": 48})
        self.assertEqual(pricing.floor_detail(food(90, shelf=96), ctx, pol(), NOW), (12000, ["D3"]))  # 90 < 96, grade A
        self.assertEqual(pricing.floor_detail(food(44, shelf=96), ctx, pol(), NOW), (12000, ["D3"]))  # 44/96 = 0.458, B
        self.assertEqual(pricing.floor_detail(food(43, shelf=96), ctx, pol(), NOW), (1, ["D3", "D4-food"]))  # C
        no_shelf = dict(food(90), shelf_hours=None)                     # shelf unknown: the median rule alone
        self.assertEqual(pricing.floor_detail(no_shelf, ctx, pol(), NOW), (1, ["D3", "D4-food"]))

    def test_stale_goods_floor_drops_to_cost(self):
        self.assertEqual(pricing.floor_detail(goods(cost=50000, age_h=6 * 24), CTX, pol(), NOW), (50000, ["D3", "D4-goods"]))
        self.assertEqual(pricing.floor(goods(cost=50000, age_h=4 * 24), CTX, pol(), NOW), 60000)


class Overpriced(unittest.TestCase):
    def test_cost_floor_above_list(self):
        p = pol(seller__floor_cost_mult=1.23)
        self.assertTrue(pricing.overpriced(goods(cost=98000), p))         # 98,000 x 1.23 > 100,000
        self.assertFalse(pricing.overpriced(goods(cost=60000), p))
        self.assertFalse(pricing.overpriced(goods(cost=85000), pol()))    # 85,000 x 1.08 < 100,000
        self.assertTrue(pricing.overpriced(goods(cost=85000), p))         # the multiplier moves the line
        self.assertFalse(pricing.overpriced(food(50, cost=19000), p))     # goods only
        self.assertFalse(pricing.overpriced(goods(cost=98000, lst=0), p))
        for bad in ({"cost": None}, {"cost": "98000"}, {"list_price": None}, {"list_price": "x"}):
            self.assertFalse(pricing.overpriced(dict(goods(cost=98000), **bad), p))    # a bad row is not a crash


class EndOfRound(unittest.TestCase):
    def test_d14_schedule(self):
        u = goods(cost=50000)
        old = pol(stock__clear_70_h=24, stock__clear_any_h=6)              # the schedule before Patch 3.3
        cases = {49: (60000, ["D3"]), 47: (50000, ["D3", "D14"]), 23: (35000, ["D3", "D14"]), 5: (1, ["D3", "D14"])}
        for h, want in cases.items():
            now = END - h * H
            self.assertEqual(pricing.floor_detail(goods(cost=50000, now=now), CTX, old, now), want, h)
        self.assertEqual(pricing.first_ask(u, CTX, old, END - 5 * H), 85000)

    def test_patch_3_3_defaults_never_sell_goods_below_cost(self):
        # stock counts at cost when the round ends: clear_70_h 0 and clear_any_h 0 leave the cost floor to the end
        for h in (47, 23, 5, 0.1):
            now = END - h * H
            self.assertEqual(pricing.floor_detail(goods(cost=50000, now=now), CTX, pol(), now), (50000, ["D3", "D14"]), h)
        p = pol(stock__clear_cost_h=0)
        self.assertEqual(pricing.floor_detail(goods(cost=50000, now=END - H), CTX, p, END - H), (60000, ["D3"]))
        # food near its end still clears (D4), whatever the end-of-round keys say
        self.assertEqual(pricing.floor(food(10, now=END - 5 * H), dict(CTX, uwh={"n": 10, "median": 24}), pol(),
                                       END - 5 * H), 1)

    def test_d14_never_raises_a_lower_floor(self):
        now = END - 47 * H
        ctx = dict(CTX, uwh={"n": 10, "median": 24})
        self.assertEqual(pricing.floor(food(10, now=now), ctx, pol(), now), 1)


class Warranty(unittest.TestCase):
    def test_only_on_request_for_a_and_b_goods(self):
        p = pol()
        self.assertEqual(pricing.warranty_terms(goods(grade="A"), True, CTX, p, NOW), {"hours": 6, "price_mult": 1.15})
        self.assertEqual(pricing.warranty_terms(goods(grade="B"), 3, CTX, p, NOW)["hours"], 3)
        self.assertEqual(pricing.warranty_terms(goods(grade="B"), 12, CTX, p, NOW)["hours"], 6)
        self.assertIsNone(pricing.warranty_terms(goods(grade="C"), True, CTX, p, NOW))
        self.assertIsNone(pricing.warranty_terms(food(50), True, CTX, p, NOW))
        self.assertIsNone(pricing.warranty_terms(goods(), None, CTX, p, NOW))
        self.assertIsNone(pricing.warranty_terms(goods(), False, CTX, p, NOW))

    def test_six_hour_cap_and_last_hours(self):
        p = pol()
        self.assertIsNone(pricing.warranty_terms(goods(), True, dict(CTX, warranted_6h=450000), p, NOW))
        self.assertIsNotNone(pricing.warranty_terms(goods(), True, dict(CTX, warranted_6h=400000), p, NOW))
        self.assertIsNone(pricing.warranty_terms(goods(), True, CTX, p, END - 7 * H))
        self.assertIsNotNone(pricing.warranty_terms(goods(), True, CTX, p, END - 9 * H))


class ClaimAndFacts(unittest.TestCase):
    def test_claim(self):
        p = pol()
        self.assertEqual(pricing.claim(goods(grade="B"), NOW, p), "B")
        self.assertEqual(pricing.claim(food(90, shelf=100), NOW, p), "A")      # 10 h above the A cut
        self.assertIsNone(pricing.claim(food(80.5, shelf=100), NOW, p))      # 0.5 h above the A cut
        self.assertEqual(pricing.claim(food(60, shelf=100), NOW, p), "B")
        self.assertEqual(pricing.claim(food(5, shelf=100), NOW, p), "C")
        self.assertIsNone(pricing.claim(dict(food(60), shelf_hours=None), NOW, p))

    def test_grade_now_recomputed_for_food(self):
        self.assertEqual(pricing.grade_now(food(90, shelf=100), NOW), "A")
        self.assertEqual(pricing.grade_now(food(30, shelf=100), NOW), "C")
        self.assertEqual(pricing.grade_now(food(-1, shelf=100), NOW), "expired")

    def test_fact_line_ends_with_the_ask(self):
        line = pricing.fact_line(food(50.25), 17000, {"grade": "B", "hours_left": 50.25, "warranty_hours": 0, "cert": True})
        for part in ("SKU G0011e806", "unit u-f", "list 20000", "grade B now", "50.2 h shelf life left", "85% of list"):
            self.assertIn(part, line)
        self.assertTrue(line.endswith("ask 17000"))
        self.assertIn("warranty 6 h included",
                      pricing.fact_line(goods(), 97750, {"grade": "A", "hours_left": None, "warranty_hours": 6, "cert": True}))


if __name__ == "__main__":
    unittest.main()
