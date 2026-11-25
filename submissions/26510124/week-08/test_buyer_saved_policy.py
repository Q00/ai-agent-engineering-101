"""Saved-oriented goals remain distinct from hard purchase authorization."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from buyer_policy import buying_terms, load_policy
from market_buyer import Buyer, PolicyRefusal


class SavedPolicyTests(unittest.TestCase):
    def terms(self, kind, *, budget=100000, price=100000, config=None):
        return buying_terms(budget, product={"list_price": price},
                            order={"round": 3, "kind": kind, "use_within_hours": 6},
                            config=config or load_policy())

    def test_goals_use_catalog_ratio_and_leave_checkout_requirements_intact(self):
        food, goods = self.terms("food"), self.terms("goods")
        self.assertEqual((food["target_price"], goods["target_price"]), (60000, 65000))
        self.assertEqual(food["accept"], {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 7})
        self.assertEqual(goods["accept"], {"cert": True, "grades": ["A", "B"], "warranty_hours": 3})
        self.assertEqual(goods["preferred_warranty_hours"], 6)
        self.assertNotIn("preferred_warranty_hours", food)

    def test_small_budget_and_integer_rounding_never_raise_price_authority(self):
        for kind in ("food", "goods"):
            self.assertEqual(self.terms(kind, budget=50000)["target_price"], 40500)
            for budget, price in ((2, 2), (101, 1000), (100000, 33333)):
                terms = self.terms(kind, budget=budget, price=price)
                self.assertLessEqual(terms["target_price"], terms["price_limit"])
                self.assertLessEqual(terms["price_limit"], min(budget * 90 // 100, price * 85 // 100))
                self.assertGreater(terms["target_price"], 0)

    def test_soft_goal_does_not_become_compulsory_rejection_or_higher_hard_limit(self):
        buyer = Buyer(Mock(), url="https://shop.invalid", item="sku", budget=100000,
                      policy=self.terms("goods"))
        buyer.validate_decision({"action": "accept", "price": 70000, "reason": "valid exact ask"}, "price: 70000")
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "accept", "price": 85001, "reason": "over cap"}, "price: 85001")

    def test_legacy_config_keeps_previous_goal(self):
        config = load_policy()
        config.pop("target_list_price_percent")
        config.pop("preferred_goods_warranty_hours")
        terms = self.terms("goods", config=config)
        self.assertEqual(terms["target_price"], 76500)
        self.assertNotIn("preferred_warranty_hours", terms)

    def test_malformed_optional_preferences_are_rejected(self):
        original = load_policy()
        for targets in (None, [], {}, {"food": 60}, {"food": True, "goods": 65},
                        {"food": 60, "goods": 101}, {"food": 60, "goods": 65, "other": 1}):
            config = {**deepcopy(original), "target_list_price_percent": targets}
            with self.subTest(targets=targets), patch("buyer_policy.read_json", return_value=config):
                with self.assertRaises(ValueError):
                    load_policy()
        for hours in (None, True, float("nan"), float("inf"), 2, 73, "6"):
            config = {**deepcopy(original), "preferred_goods_warranty_hours": hours}
            with self.subTest(hours=hours), patch("buyer_policy.read_json", return_value=config):
                with self.assertRaises(ValueError):
                    load_policy()


if __name__ == "__main__":
    unittest.main()
