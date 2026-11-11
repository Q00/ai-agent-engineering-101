import unittest
from datetime import datetime

import restock
import round_clock
import seller_r3


class RoundClockTest(unittest.TestCase):
    def test_hours_left_counts_down_to_the_close(self):
        self.assertAlmostEqual(47.98, round_clock.hours_left(datetime(2026, 11, 16, 0, 0, tzinfo=round_clock.KST)), places=2)
        self.assertLess(round_clock.hours_left(datetime(2026, 11, 18, 0, 0, tzinfo=round_clock.KST)), 0)

    def test_restock_stops_food_then_everything_before_the_close(self):
        catalog = {"K1": {"category": "food>kimchi", "kind": "food", "list_price": 20_000},
                   "B1": {"category": "home>bed", "kind": "goods", "list_price": 300_000}}
        demand = [{"category": "food>kimchi", "median_price": 20_000}, {"category": "home>bed", "median_price": 300_000}]
        stats = {"categories": {"food>kimchi": {"n": 50, "median_ratio": 0.9, "revenue": 900_000, "median_amount": 18_000},
                                "home>bed": {"n": 50, "median_ratio": 0.8, "revenue": 12_000_000, "median_amount": 240_000}}, "skus": {}}
        inv = {"balance": 5_000_000, "units": []}
        self.assertEqual({"K1", "B1"}, {b["sku"] for b in restock.plan(inv, catalog, demand, [], {}, stats, hours_to_end=100)})
        self.assertEqual(["B1"], [b["sku"] for b in restock.plan(inv, catalog, demand, [], {}, stats, hours_to_end=30)])
        report = {}
        self.assertEqual([], restock.plan(inv, catalog, demand, [], {}, stats, report, hours_to_end=20))
        self.assertIn("no more stock", report["liquidation"])

    def test_top_category_may_hold_more_when_it_closes_and_small_margins_are_not_topped_up(self):
        catalog = {"S1": {"category": "home>sofa", "kind": "goods", "list_price": 1_000_000},
                   "J1": {"category": "food>juice", "kind": "food", "list_price": 8_000}}
        demand = [{"category": "home>sofa", "median_price": 1_000_000}, {"category": "food>juice", "median_price": 8_000}]
        stats = {"categories": {"home>sofa": {"n": 50, "median_ratio": 0.8, "revenue": 40_000_000, "median_amount": 800_000},
                                "food>juice": {"n": 50, "median_ratio": 0.8, "revenue": 320_000, "median_amount": 6_400}}, "skus": {}}
        units = [{"category": "home>sofa", "sku": "S1", "cost": 600_000, "list_price": 1_000_000}] * 2 \
            + [{"category": "food>juice", "sku": "J1", "cost": 4_000, "list_price": 8_000}]
        inv = {"balance": 5_000_000, "units": units}                      # sofas 1.2M of a 2.5M cap: over 35%, under 55%
        closes = {"home>sofa": {"opens": 20, "deals": 12, "close_rate": 0.6}}
        self.assertEqual([], [b["sku"] for b in restock.plan(inv, catalog, demand, [], {}, stats)])
        report = {}
        self.assertEqual(["S1"], [b["sku"] for b in restock.plan(inv, catalog, demand, [], {}, stats, report, closes)])
        self.assertEqual(["juice"], report["skipped_small_margin"])      # 8k x (0.8 - 0.6) = 1.6k a unit: not worth topping up

    def test_seller_sells_down_to_cost_but_never_below_it_at_the_close(self):
        unit = {"sku": "G1", "kind": "goods", "cost": 1000, "list_price": 2000, "grade": "A", "expires_at": None}
        p = {"seller_volume_share": 1.0, "seller_markup": 1.0}
        normal = seller_r3.price_plan(unit, "t", params=p, market=(1500.0, 5, "sku"), hours_to_end=100)
        self.assertEqual((1500, 1440, "even"), (normal["ask"], normal["floor"], normal["regime"]))
        for hours in (20, 10, 2):   # patch 3.4: unsold stock keeps its cost on the Book, so the floor is the cost
            down = seller_r3.price_plan(unit, "t", params=p, market=(1500.0, 5, "sku"), hours_to_end=hours)
            self.assertEqual((1425, 1000, "sell-down"), (down["ask"], down["floor"], down["regime"]), hours)

    def test_slow_category_prices_on_the_volume_arm_even_on_the_margin_arm(self):
        unit = {"sku": "G1", "kind": "goods", "cost": 1000, "list_price": 2000, "grade": "A", "expires_at": None}
        p = {"seller_volume_share": 0.0, "seller_markup": 1.0}            # margin arm
        plan = seller_r3.price_plan(unit, "t", params=p, market=(1500.0, 5, "sku"), pressure={"opens": 20, "deals": 5, "close_rate": 0.25})
        self.assertEqual((1455, "slow"), (plan["ask"], plan["regime"]))  # 1500 x 1.00 x 0.97, not x 1.05


if __name__ == "__main__":
    unittest.main()
