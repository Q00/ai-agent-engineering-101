"""Relaxed purchasing with strict personal ratings; synthetic, no live calls."""
import base64
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_policy import buying_terms, checkout_policy, load_policy
from buyer_selection import shop_evidence
from harness import buyer_decision
from market_buyer import Buyer, PolicyRefusal, BuyerError
from payment_memory import attach_reviews
from review_settled import review
from test_buyer_selection import shop
from vendor import market_kit_v4 as kit


class BalancedBuyerTests(unittest.TestCase):
    def terms(self, budget=100000, list_price=100000):
        return buying_terms(budget, product={"list_price": list_price},
                            order={"round": 3, "kind": "goods"})

    def test_new_limits_preserve_separate_frozen_ideal_criteria(self):
        terms = self.terms()
        self.assertEqual(terms["price_limit"], 85000)
        self.assertEqual(terms["target_price"], 65000)
        self.assertEqual(terms["review_preferences"]["price_limit"], 65000)
        self.assertEqual(terms["review_preferences"]["excellent_price"], 58500)
        self.assertEqual(self.terms(80000)["price_limit"], 72000)
        self.assertEqual(self.terms(list_price=80000)["price_limit"], 68000)
        self.assertEqual(self.terms(101, 1000)["price_limit"], 90)
        config = load_policy()
        terms = buying_terms(100000, product={"list_price": 100000},
                             order={"round": 3, "kind": "goods"}, config=config)
        config["review_preferences"]["grades"].append("C")
        self.assertEqual(terms["review_preferences"]["grades"], ["A"])

    def test_signed_checkout_allows_b_three_hours_but_keeps_minimum_guards(self):
        key = Ed25519PrivateKey.generate()
        order = {"kind": "goods", "market_pubkey": kit.pub_b64(key)}
        policy = checkout_policy(order)
        for grade, hours, valid in (("A", 3, True), ("B", 3, True), ("B", 2, False), ("C", 6, False)):
            with self.subTest(grade=grade, hours=hours):
                body = {"unit": "u1", "sku": "G00001", "grade": grade}
                cert = {"cert": body, "sig": base64.b64encode(key.sign(kit.canon(body))).decode()}
                checkout = {"unit": "u1", "item": "G00001", "cert": cert, "warranty_hours": hours}
                self.assertEqual(kit.check_accept(order, checkout, policy) is None, valid)
        self.assertIsNotNone(kit.check_accept(order, {"item": "G00001", "warranty_hours": 6}, policy))

    def test_exact_acceptance_can_reach_limit_without_relaxing_counteroffer_or_receipt(self):
        buyer = Buyer(Mock(), url="https://fixture.invalid", item="G00001", budget=100000, policy=self.terms())
        buyer.authorized_price = 75000
        buyer.validate_decision({"action": "accept", "price": 85000, "reason": "acceptable"}, "asking 85000")
        buyer.validate_decision({"action": "offer", "price": 85000, "reason": "meet ask"}, "asking 85000")
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "offer", "price": 84000, "reason": "jump"}, "asking 85000")
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "accept", "price": 85001, "reason": "over limit"}, "asking 85001")
        with self.assertRaises(BuyerError):
            buyer.validate_decision({"action": "accept", "price": 84000, "reason": "different ask"}, "asking 85000")

    def test_rating_preferences_do_not_enter_model_context(self):
        terms = self.terms()
        original = deepcopy(terms)
        def model(prompt, context, validate, **options):
            self.assertNotIn("review_preferences", context["purchase_policy"])
            self.assertEqual(context["purchase_policy"]["price_limit"], 85000)
            self.assertEqual(context["decisions_left"], 1)
            return validate('{"action":"accept","price":85000,"reason":"meets minimums"}')
        with patch("harness.decide", side_effect=model) as called:
            result = buyer_decision(item="G00001", budget=100000, seller_message="asking 85000",
                                    history=[], policy=terms, current_ask=85000, decisions_left=1)
        self.assertEqual(result["price"], 85000)
        self.assertEqual(terms, original)
        self.assertEqual(called.call_count, 1)

    def test_four_decisions_can_end_in_acceptance_at_limit(self):
        def task(price):
            return {"status": {"state": "TASK_STATE_INPUT_REQUIRED",
                               "message": {"parts": [{"text": f"Grade B; warranty 3 hours; asking {price}"}]}}}
        buyer = Buyer(Mock(), url="https://fixture.invalid", item="G00001", budget=100000, policy=self.terms())
        decisions = [{"action": "offer", "price": price, "reason": "negotiate"}
                     for price in (75000, 77000, 79000)]
        decisions.append({"action": "accept", "price": 85000, "reason": "acceptable minimums"})
        with patch.object(buyer, "discover"), patch.object(buyer, "verify_receipt") as verified, \
                patch.object(buyer, "send", side_effect=[task(p) for p in (90000, 88000, 87000, 85000)] + [
                    {"status": {"state": "TASK_STATE_COMPLETED"}}]), \
                patch("market_buyer.buyer_decision", side_effect=decisions) as model:
            self.assertEqual(buyer.negotiate(), "receipt_issued")
        self.assertEqual(model.call_count, 4)
        self.assertEqual([call.kwargs["decisions_left"] for call in model.call_args_list], [4, 3, 2, 1])
        self.assertEqual(buyer.authorized_price, 85000)
        verified.assert_called_once()

    def test_preference_only_low_rating_is_not_remembered_as_contract_failure(self):
        terms = self.terms()
        deal = {"order": {"order": "o1", "kind": "goods"},
                "receipt": {"item": "G00001", "price": 80000, "task": "t1"},
                "settled": {"ledger": 17, "sku": "G00001", "grade": "B", "warranty_hours": 3,
                            "claim_honest": True, "value": 100000}}
        record = {"name": "shop", "order": "o1", "item": "G00001", "price": 80000,
                  "result": "settled", "observed_receipt": deal["receipt"], "stars": 3}
        assessment = review(deal, terms=terms)
        self.assertEqual(assessment["stars"], 2)
        self.assertEqual(assessment["issues"], [])
        rows = [{"order": "o1", "receipt": deal["receipt"], "assessment": assessment}]
        updated = attach_reviews([record], rows)
        evidence = shop_evidence(shop("shop"), updated)["own_memory"]
        self.assertEqual(evidence["accepted_tradeoffs"], 1)
        self.assertEqual(evidence["unsatisfactory_settlements"], 0)
        self.assertNotIn("review_issues", record)
        pending = attach_reviews([{**record, "result": "receipt_issued"}], rows)[0]
        self.assertNotIn("review_issues", pending)
        deal["settled"]["claim_honest"] = False
        rows[0]["assessment"] = review(deal, terms=terms)
        faulty = shop_evidence(shop("shop"), attach_reviews([record], rows))["own_memory"]
        self.assertEqual(faulty["unsatisfactory_settlements"], 1)
        self.assertEqual(faulty["accepted_tradeoffs"], 0)

    def test_food_required_use_and_original_buffer_are_still_required(self):
        terms = buying_terms(100000, product={"list_price": 100000},
                            order={"round": 3, "kind": "food", "use_within_hours": 4})
        self.assertEqual(terms["accept"], {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 5})
        self.assertEqual(terms["review_preferences"]["food_min_hours_left"], 5)


if __name__ == "__main__":
    unittest.main()
