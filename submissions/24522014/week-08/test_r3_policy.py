import unittest
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
