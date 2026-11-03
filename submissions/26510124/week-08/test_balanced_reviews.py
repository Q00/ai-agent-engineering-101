"""Settled purchases distinguish accepted compromises from actual problems."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from review_settled import purchase_terms, review


def balanced_terms():
    # Explicit historical fixture: tests cannot change when local policy does.
    return {
        "version": "balanced-purchase-strict-review-v2", "price_limit": 85000,
        "target_price": 58500, "kind": "goods",
        "accept": {"cert": True, "grades": ["A", "B"], "warranty_hours": 3},
        "review_preferences": {
            "price_limit": 65000, "excellent_price": 58500, "grades": ["A"],
            "goods_warranty_hours": 6,
        },
    }


class BalancedReviewTests(unittest.TestCase):
    def setUp(self):
        self.terms = balanced_terms()
        self.deal = {
            "order": {"order": "o1", "budget": 100000, "kind": "goods"},
            "receipt": {"item": "G00001", "price": 62000, "task": "t1"},
            "settled": {"ledger": 17, "sku": "G00001", "grade": "A",
                        "warranty_hours": 6, "shop": "https://fixture.invalid",
                        "claim_honest": True, "value": 100000},
        }

    def test_ideal_deal_is_four_exceptional_price_is_five(self):
        for price, stars in ((65000, 4), (58501, 4), (58500, 5)):
            with self.subTest(price=price):
                self.deal["receipt"]["price"] = price
                result = review(self.deal, terms=self.terms, now=1000)
                self.assertEqual(result["stars"], stars)
                self.assertEqual(result["issues"], [])
                self.assertEqual(result["preference_shortfalls"], [])

    def test_each_single_accepted_compromise_is_three_not_a_breach(self):
        for where, field, value, code in (
            ("receipt", "price", 80000, "above_preferred_price"),
            ("settled", "grade", "B", "preferred_grade_not_met"),
            ("settled", "warranty_hours", 3, "preferred_warranty_not_met"),
        ):
            with self.subTest(field=field):
                deal = deepcopy(self.deal)
                deal[where][field] = value
                result = review(deal, terms=self.terms)
                self.assertEqual(result["stars"], 3)
                self.assertEqual(result["issues"], [])
                self.assertEqual(result["preference_shortfalls"], [code])
                self.assertEqual(result["reasons"], [code])
                self.assertIn("accepted", result["review"])
                for unsupported in ("dishonest", "fraud", "did not meet my quality requirement",
                                    "shorter than required", "claim did not match"):
                    self.assertNotIn(unsupported, result["review"])

    def test_multiple_accepted_compromises_are_two_without_inventing_faults(self):
        self.deal["receipt"]["price"] = 80000
        self.deal["settled"]["grade"] = "B"
        result = review(self.deal, terms=self.terms)
        self.assertEqual(result["stars"], 2)
        self.assertEqual(result["issues"], [])
        self.assertEqual(set(result["preference_shortfalls"]),
                         {"above_preferred_price", "preferred_grade_not_met"})
        self.deal["settled"]["warranty_hours"] = 3
        result = review(self.deal, terms=self.terms)
        self.assertEqual(result["stars"], 2)
        self.assertEqual(len(result["preference_shortfalls"]), 3)

    def test_hard_quality_warranty_and_missing_fact_are_two(self):
        for field, value, reason in (
            ("grade", "C", "grade_below_requirement"),
            ("warranty_hours", 2, "short_warranty"),
            ("grade", None, "grade_unreported"),
            ("warranty_hours", None, "warranty_unreported"),
        ):
            with self.subTest(field=field, value=value):
                deal = deepcopy(self.deal)
                deal["settled"][field] = value
                result = review(deal, terms=self.terms)
                self.assertEqual(result["stars"], 2)
                self.assertIn(reason, result["issues"])
                self.assertEqual(result["preference_shortfalls"], [])

    def test_proven_false_claim_zero_value_and_hard_price_breach_are_one(self):
        for where, field, value, reason in (
            ("settled", "claim_honest", False, "claim_mismatch"),
            ("settled", "value", 0, "zero_value"),
            ("receipt", "price", 85001, "above_price_limit"),
        ):
            with self.subTest(field=field):
                deal = deepcopy(self.deal)
                deal[where][field] = value
                result = review(deal, terms=self.terms)
                self.assertEqual(result["stars"], 1)
                self.assertIn(reason, result["issues"])

    def test_public_review_keeps_private_budget_and_targets_hidden(self):
        self.deal["receipt"]["price"] = 80000
        self.deal["settled"]["grade"] = "B"
        result = review(self.deal, terms=self.terms)
        for private in (100000, 85000, 65000, 58500):
            self.assertNotIn(str(private), result["review"])
        self.assertIn("80000", result["review"])

    def test_matching_original_preferences_survive_local_config_changes(self):
        record = {"order": "o1", "url": "https://fixture.invalid", "item": "G00001",
                  "price": 62000, "observed_receipt": self.deal["receipt"],
                  "result": "receipt_issued", "purchase_policy": deepcopy(self.terms)}
        self.deal["settled"]["grade"] = "B"
        before = deepcopy(record)
        with patch("buyer_policy.load_policy", side_effect=AssertionError("Do not reload policy")):
            terms = purchase_terms(self.deal, [record])
            result = review(self.deal, terms=terms)
        self.assertEqual(result["stars"], 3)
        self.assertEqual(record, before)
        self.assertIsNone(purchase_terms(self.deal, [{**record, "url": "https://other.invalid"}]))

    def test_legacy_policy_retains_original_strict_assessment(self):
        legacy = {"version": "very-strict-v1", "price_limit": 65000,
                  "target_price": 58500, "kind": "goods",
                  "accept": {"cert": True, "grades": ["A"], "warranty_hours": 6}}
        for price, stars in ((60000, 2), (58000, 4), (46000, 5)):
            self.deal["receipt"]["price"] = price
            self.assertEqual(review(self.deal, terms=legacy)["stars"], stars)
        self.deal["settled"]["grade"] = "B"
        result = review(self.deal, terms=legacy)
        self.assertEqual(result["stars"], 2)
        self.assertIn("grade_below_requirement", result["issues"])

    def test_missing_original_terms_never_imports_current_minima(self):
        self.deal["receipt"]["price"] = 90000
        self.deal["settled"].update(grade="C", warranty_hours=0)
        with patch("buyer_policy.load_policy", side_effect=AssertionError("No retroactive policy")):
            result = review(self.deal)
        self.assertEqual(result["stars"], 3)
        self.assertEqual(result["issues"], ["price_context_unavailable"])
        self.assertEqual(result["preference_shortfalls"], [])
        self.assertNotIn("shorter than required", result["review"])
        self.deal["settled"]["claim_honest"] = False
        self.assertEqual(review(self.deal)["stars"], 1)

    def test_food_uses_frozen_margin_and_identifies_review_time(self):
        self.terms.update(kind="food", accept={"cert": True, "grades": ["A", "B"], "min_hours_left": 3})
        self.terms["review_preferences"]["food_min_hours_left"] = 3
        self.deal["order"].update(kind="food", use_within_hours=2)
        self.deal["settled"]["expires_at"] = 1000 + 3 * 3600
        self.assertEqual(review(self.deal, terms=self.terms, now=1000)["stars"], 4)
        self.deal["settled"]["expires_at"] = 1000 + 2.5 * 3600
        result = review(self.deal, terms=self.terms, now=1000)
        self.assertEqual(result["stars"], 2)
        self.assertIn("short_shelf_life_at_review", result["issues"])
        self.assertIn("at review time", result["review"])
        self.deal["settled"]["expires_at"] = 999
        result = review(self.deal, terms=self.terms, now=1000)
        self.assertEqual(result["stars"], 2)
        self.assertIn("does not establish its condition at payment", result["review"])

    def test_missing_preferred_price_does_not_invent_expensive_purchase(self):
        self.terms["review_preferences"].update(price_limit=None, excellent_price=None)
        result = review(self.deal, terms=self.terms)
        self.assertEqual(result["stars"], 3)
        self.assertIn("price_context_unavailable", result["issues"])
        self.assertNotIn("above_preferred_price", result["reasons"])


if __name__ == "__main__":
    unittest.main()
