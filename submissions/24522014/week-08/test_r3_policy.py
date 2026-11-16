import unittest
from datetime import datetime
from unittest.mock import patch
from r3_policy import buyer_accept, selling_terms


class Policy(unittest.TestCase):
    def unit(self, **changes):
        return dict({"unit": "u1", "sku": "G1", "kind": "goods", "grade": "A",
                     "cost": 101, "list_price": 200, "supplied_at": 0, "expires_at": None}, **changes)

    def test_goods_discount_stages_and_rounding(self):
        self.assertEqual(selling_terms(self.unit(), now=0)["quote"], 200)
        self.assertEqual(selling_terms(self.unit(), now=24*3600)["quote"], 180)
        self.assertEqual(selling_terms(self.unit(), now=48*3600)["quote"], 160)
        self.assertEqual(selling_terms(self.unit(), now=72*3600)["quote"], 140)
        self.assertEqual(selling_terms(self.unit(), now=168*3600)["floor"], 86)
        self.assertEqual(selling_terms(self.unit(), now=336*3600)["floor"], 71)

    def test_food_remaining_life_discounts(self):
        catalog = {"G1": {"shelf_hours": 100}}
        cost = self.unit()["cost"]
        for remaining, stage, quote in ((9, 1, cost), (5, 1, cost)):
            t = selling_terms(self.unit(kind="food", expires_at=remaining*3600), catalog, now=0)
            self.assertEqual((t["discount_stage"], t["quote"], t["floor"]), (stage, quote, cost))
            self.assertFalse(t["below_cost_allowed"])

    def test_expired_or_unknown_shelf_life(self):
        with self.assertRaises(ValueError):
            selling_terms(self.unit(kind="food", expires_at=1), now=2)
        self.assertFalse(selling_terms(self.unit(kind="food", expires_at=100), now=0)["below_cost_allowed"])

    def test_buyer_certificate_grade_and_food_margin(self):
        self.assertEqual(buyer_accept({"kind": "food", "use_within_hours": 3}),
                         {"cert": True, "grades": ["A", "B"], "min_hours_left": 5})
        with self.assertRaises(ValueError):
            buyer_accept({"kind": "food"})


class RoundEnd(unittest.TestCase):
    """The user's 11-16 choice ("2시간 전까지 구매, 정리 판매 끔 (권장)"): no below-cost round-end clearance; age stages stay."""

    END = datetime.fromisoformat("2026-11-17T23:59:00+09:00").timestamp()
    RUNTIME = {"version": "t", "controls": {"quote_multipliers": {}}}

    def terms(self, hours_left, clearance=None, age_hours=1):
        import r3_policy
        policy = _shipped_load_policy()
        if clearance is None:
            policy["selling"].pop("round_end_clearance_hours", None)
        else:
            policy["selling"]["round_end_clearance_hours"] = clearance
        now = self.END - hours_left * 3600
        unit = {"unit": "u1", "sku": "G1", "kind": "goods", "grade": "A", "cost": 101, "list_price": 200,
                "supplied_at": now - age_hours * 3600, "expires_at": None}
        with patch.object(r3_policy, "load_policy", return_value=policy):
            return selling_terms(unit, now=now, runtime=self.RUNTIME)

    def test_off_keeps_the_ordinary_floor_to_the_end(self):
        for hours_left in (23, 12, 5, 0.5):
            with self.subTest(hours_left=hours_left):
                t = self.terms(hours_left, clearance=0)
                self.assertEqual((t["floor"], t["quote"], t["discount_reason"]), (107, 200, "standard"))
                self.assertFalse(t["below_cost_allowed"])

    def test_the_old_window_when_on_missing_or_bad(self):
        for clearance in (24, None, "0", -1, 25, float("nan"), True):
            with self.subTest(clearance=clearance):
                self.assertEqual((self.terms(12, clearance)["floor"], self.terms(3, clearance)["floor"]), (86, 71))
                self.assertEqual(self.terms(12, clearance)["discount_reason"], "round_end_clearance")
                self.assertEqual(self.terms(25, clearance)["floor"], 107)

    def test_long_stock_loss_stage_still_applies_with_clearance_off(self):
        t = self.terms(3, clearance=0, age_hours=170)
        self.assertEqual((t["floor"], t["quote"], t["discount_reason"]), (86, 86, "long_stock_loss_clearance"))
        self.assertTrue(t["below_cost_allowed"])

    def test_shipped_values(self):
        selling = _shipped_load_policy()["selling"]
        self.assertEqual(selling["round_end"], "2026-11-17T23:59:00+09:00")
        self.assertEqual((selling["round_end_purchase_stop_hours"], selling["round_end_clearance_hours"]), (2, 0))
        self.assertEqual(selling["cost_multipliers"], [1.0, 0.85, 0.7])
        self.assertEqual(selling["goods_loss_hours"], [168, 336])


import r3_policy as _r3p_floor
_shipped_load_policy = _r3p_floor.load_policy


def _without_food_floor():
    # These tests pin today's food prices: the shipped food floor (cost x 1.08, on since 11-10) must not move them.
    policy = _shipped_load_policy()
    policy["selling"]["food_pricing_v2"]["floor_cost_multiple"] = None
    return policy


def setUpModule():
    _r3p_floor.load_policy = _without_food_floor


def tearDownModule():
    _r3p_floor.load_policy = _shipped_load_policy


if __name__ == "__main__":
    unittest.main()
