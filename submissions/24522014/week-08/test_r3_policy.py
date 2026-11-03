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
        for remaining, stage, quote in ((44, 1, 101), (19, 2, 86), (9, 3, 71)):
            t = selling_terms(self.unit(kind="food", expires_at=remaining*3600), catalog, now=0)
            self.assertEqual((t["discount_stage"], t["quote"]), (stage, quote))

    def test_expired_or_unknown_shelf_life(self):
        with self.assertRaises(ValueError):
            selling_terms(self.unit(kind="food", expires_at=1), now=2)
        self.assertFalse(selling_terms(self.unit(kind="food", expires_at=100), now=0)["below_cost_allowed"])

    def test_buyer_certificate_grade_and_food_margin(self):
        self.assertEqual(buyer_accept({"kind": "food", "use_within_hours": 3}),
                         {"cert": True, "grades": ["A", "B"], "min_hours_left": 5})
        with self.assertRaises(ValueError):
            buyer_accept({"kind": "food"})


if __name__ == "__main__":
    unittest.main()
