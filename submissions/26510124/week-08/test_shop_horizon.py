"""Pure, fixed-clock horizon policies: no market, model, service, or state I/O."""
from copy import deepcopy
from datetime import datetime
import unittest

from shop_horizon import DEFAULT_CONFIG, clearance_bounds, phase, purchase_window, validate


class HorizonTests(unittest.TestCase):
    def setUp(self):
        self.cfg = deepcopy(DEFAULT_CONFIG)
        self.end = datetime.fromisoformat(self.cfg["ends_at"]).timestamp()
        self.goods = {"kind": "goods", "cost": 70000, "list_price": 100000, "grade": "A"}
        self.food = {**self.goods, "kind": "food", "expires_at": None}
        self.terms = {"price_floor": 77000, "closing_price_floor": 77000, "warranty_hours": 6}
        self.fast = {"mature": 3, "pending": 0, "unknown": 0, "mean_hours": 1}

    def at(self, remaining):
        return self.end - remaining * 3600

    def test_schedule_has_explicit_timezone_and_rejects_missing_invalid_configuration(self):
        validate(self.cfg)
        bad = [None, {}, {**self.cfg, "ends_at": "2026-11-17T23:59:00"},
               {**self.cfg, "ends_at": "not a date"}, {**self.cfg, "round": True},
               {**self.cfg, "stop_buying_hours": 25}, {**self.cfg, "goods_warranty_buffer_hours": 5},
               {**self.cfg, "wind_down_hours": 6, "stop_buying_hours": 12},
               {**self.cfg, "sell_time_safety_factor": float("nan")}]
        for config in bad:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    validate(config)
                self.assertFalse(phase(config, 3, self.at(100))["allow_purchases"])
                self.assertIsNone(clearance_bounds(config, 3, self.goods, self.terms, self.at(10)))

    def test_equivalent_timezone_is_same_deadline(self):
        utc = {**self.cfg, "ends_at": "2026-11-17T14:59:00+00:00"}
        self.assertEqual(phase(utc, 3, self.at(24))["phase"], "wind_down")
        self.assertEqual(phase(utc, 3, self.at(6))["remaining_hours"], 6)

    def test_exact_phase_boundaries_and_ended_never_reopen_buying(self):
        for hours, expected, buy, explore in ((24.001, "normal", True, True),
                (24, "wind_down", True, False), (6.001, "wind_down", True, False),
                (6, "stop_buying", False, False), (0, "ended", False, False),
                (-1000, "ended", False, False)):
            with self.subTest(hours=hours):
                actual = phase(self.cfg, 3, self.at(hours))
                self.assertEqual((actual["phase"], actual["allow_purchases"], actual["allow_exploration"]),
                                 (expected, buy, explore))
                if not buy:
                    self.assertFalse(purchase_window(self.cfg, 3, self.food, self.fast, self.at(hours))[0])

    def test_round_mismatch_invalid_clock_and_old_fixture_have_no_clearance(self):
        for rnd in (None, True, "3", 2, 4):
            self.assertEqual(phase(self.cfg, rnd, self.at(10))["reason"], "round_mismatch")
            self.assertFalse(purchase_window(self.cfg, rnd, self.goods, self.fast, self.at(100))[0])
            self.assertIsNone(clearance_bounds(self.cfg, rnd, self.goods, self.terms, self.at(10)))
        for now in (None, True, -1, float("nan"), float("inf")):
            self.assertEqual(phase(self.cfg, 3, now)["reason"], "invalid_clock")
        # Older epoch fixtures remain ordinary operation, never end-of-round discounts.
        self.assertEqual(phase(self.cfg, 3, 1700000000)["phase"], "normal")
        self.assertIsNone(clearance_bounds(self.cfg, 3, self.goods, self.terms, 1700000000))

    def test_unknown_goods_need_55_hours_and_food_13_with_safety_and_buffers(self):
        # goods: 24*2 + 6 warranty + 1 settlement; food: 6*2 + 1.
        self.assertEqual(purchase_window(self.cfg, 3, self.goods, {}, self.at(55)),
                         (True, "fallback_turnover_window"))
        self.assertFalse(purchase_window(self.cfg, 3, self.goods, {}, self.at(54.999))[0])
        # Extend exploration to inspect the food fallback boundary independently.
        cfg = {**self.cfg, "wind_down_hours": 6}
        self.assertEqual(purchase_window(cfg, 3, self.food, {}, self.at(13)),
                         (True, "fallback_turnover_window"))
        self.assertFalse(purchase_window(cfg, 3, self.food, {}, self.at(12.999))[0])

    def test_last_day_rejects_exploration_even_with_short_food_fallback(self):
        self.assertEqual(purchase_window(self.cfg, 3, self.food, {}, self.at(24)),
                         (False, "unproven_near_round_end"))
        self.assertTrue(purchase_window(self.cfg, 3, self.food, {}, self.at(24.001))[0])
        for patch in ({"mature": 2}, {"pending": 1}, {"unknown": 1},
                      {"mean_hours": float("nan")}, {"mean_hours": -1}):
            self.assertEqual(purchase_window(self.cfg, 3, self.food, {**self.fast, **patch}, self.at(20)),
                             (False, "unproven_near_round_end"))

    def test_mature_mean_is_doubled_with_minimum_and_all_goods_warranty_buffer(self):
        self.assertTrue(purchase_window(self.cfg, 3, self.goods, self.fast, self.at(9))[0])
        self.assertFalse(purchase_window(self.cfg, 3, self.goods, self.fast, self.at(8.999))[0])
        for grade in ("A", "B", "C"):
            self.assertFalse(purchase_window(self.cfg, 3, {**self.goods, "grade": grade},
                                             self.fast, self.at(8))[0])
        zero = {**self.fast, "mean_hours": 0}
        self.assertTrue(purchase_window(self.cfg, 3, self.goods, zero, self.at(8))[0])
        self.assertFalse(purchase_window(self.cfg, 3, self.goods, zero, self.at(7.999))[0])
        self.assertTrue(purchase_window(self.cfg, 3, self.food, self.fast, self.at(6.001))[0])
        self.assertFalse(purchase_window(self.cfg, 3, self.food, self.fast, self.at(6))[0])

    def test_malformed_products_or_economics_cannot_invent_fast_turnover(self):
        for product in (None, {}, {"kind": "mystery"}):
            self.assertEqual(purchase_window(self.cfg, 3, product, self.fast, self.at(100)),
                             (False, "unknown_product_kind"))
        for metrics in (None, [], {**self.fast, "mature": True}, {**self.fast, "mean_hours": "0"}):
            self.assertFalse(purchase_window(self.cfg, 3, self.goods, metrics, self.at(50))[0])

    def test_clearance_preserves_normal_floor_warranty_and_inputs(self):
        original = deepcopy((self.cfg, self.goods, self.terms))
        b = clearance_bounds(self.cfg, 3, self.goods, self.terms, self.at(24))
        self.assertEqual(b, {"minimum": 77000, "maximum": 77770, "target": 77770,
                             "source": "round_end_stock_clearance"})
        self.assertEqual((self.cfg, self.goods, self.terms), original)
        for hours in (24.001, 0, -1):
            self.assertIsNone(clearance_bounds(self.cfg, 3, self.goods, self.terms, self.at(hours)))
        self.assertEqual(clearance_bounds(self.cfg, 3, self.goods, self.terms, self.at(1)), b)

    def test_clearance_list_price_caps_and_invalid_floor_fail_closed(self):
        for price, expected in ((77500, 77500), (70000, 77000)):
            product = {**self.goods, "list_price": price}
            self.assertEqual(clearance_bounds(self.cfg, 3, product, self.terms, self.at(10))["maximum"], expected)
        for terms in (None, {}, {**self.terms, "price_floor": 60000}, {**self.terms, "price_floor": True}):
            self.assertIsNone(clearance_bounds(self.cfg, 3, self.goods, terms, self.at(10)))
        for product in (None, {}, {**self.goods, "list_price": float("nan")}):
            self.assertIsNone(clearance_bounds(self.cfg, 3, product, self.terms, self.at(10)))


if __name__ == "__main__":
    unittest.main()
