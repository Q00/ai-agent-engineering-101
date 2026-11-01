import base64
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_policy import buying_terms, checkout_policy
from harness import HarnessError, buyer_decision
from market_buyer import Buyer, PolicyRefusal, ReceiptMismatch
from payment_memory import attach_reviews
from review_settled import review, purchase_terms
from vendor import market_kit_v4 as kit


class StrictBuyerTests(unittest.TestCase):
    def terms(self, budget=100000, list_price=100000):
        return buying_terms(budget, product={"list_price": list_price}, order={"round": 3, "kind": "goods"})

    def test_both_caps_apply_with_integer_flooring(self):
        self.assertEqual(self.terms()["price_limit"], 65000)
        self.assertEqual(self.terms(80000)["price_limit"], 56000)
        self.assertEqual(self.terms(list_price=80000)["price_limit"], 52000)
        self.assertEqual(self.terms(101, 1000)["price_limit"], 70)

    def test_missing_catalog_never_silently_drops_price_condition(self):
        with self.assertRaises(ValueError):
            buying_terms(100000, order={"round": 3, "kind": "goods"})

    def test_model_cannot_bid_above_limit_even_inside_full_budget(self):
        terms = self.terms()
        def model(_, context, validator, **kwargs):
            self.assertEqual(context["purchase_policy"]["price_limit"], 65000)
            return validator('{"action":"offer","price":65001,"reason":"seller demanded it"}')
        with patch("harness.decide", side_effect=model), self.assertRaises(HarnessError):
            buyer_decision(item="G00001", budget=100000, seller_message="asking 65001", history=[], policy=terms)
        buyer = Buyer(Mock(), url="https://fixture.invalid", item="G00001", budget=100000, policy=terms)
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "accept", "price": 65001, "reason": "pressure"}, "asking 65001")

    def test_concessions_are_small_and_receipt_guard_uses_same_limit(self):
        buyer = Buyer(Mock(), url="https://fixture.invalid", item="G00001", budget=100000, policy=self.terms())
        buyer.authorized_price = 50000
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "offer", "price": 60000, "reason": "rush"}, "asking 60000")
        buyer.authorized_price = 65001
        with self.assertRaises(ReceiptMismatch):
            buyer.verify_receipt({"artifacts": [{"parts": [{"data": {"item": "G00001", "price": 65001}}]}]})

    def test_four_rounds_end_with_cancellation_and_no_forced_acceptance(self):
        buyer = Buyer(Mock(), url="https://fixture.invalid", item="G00001", budget=100000, policy=self.terms())
        buyer.task_id = "t"
        state = {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": "asking 90000"}]}}}
        with patch.object(buyer, "discover"), patch.object(buyer, "send", return_value=state) as send, \
                patch.object(buyer, "cancel") as cancel, \
                patch("market_buyer.buyer_decision", return_value={"action": "offer", "price": 50000, "reason": "firm"}) as model:
            self.assertEqual(buyer.run(), "no_deal")
        self.assertEqual(model.call_count, 4)
        self.assertEqual(send.call_count, 5)  # first SKU request + four numeric offers
        cancel.assert_called_once()
        self.assertIsNone(buyer.receipt)

    def test_v4_rejects_grade_b_or_short_warranty_and_accepts_grade_a_six_hours(self):
        key = Ed25519PrivateKey.generate()
        order = {"kind": "goods", "market_pubkey": kit.pub_b64(key)}
        policy = checkout_policy(order)
        for grade, hours, valid in [("B", 6, False), ("A", 5, False), ("A", 6, True)]:
            with self.subTest(grade=grade, hours=hours):
                body = {"unit": "u1", "sku": "G00001", "grade": grade}
                cert = {"cert": body, "sig": base64.b64encode(key.sign(kit.canon(body))).decode()}
                co = {"unit": "u1", "item": "G00001", "cert": cert, "warranty_hours": hours}
                self.assertEqual(kit.check_accept(order, co, policy) is None, valid)


class StrictReviewTests(unittest.TestCase):
    def setUp(self):
        self.terms = buying_terms(100000, product={"list_price": 100000}, order={"round": 3, "kind": "goods"})
        self.deal = {"order": {"order": "o1", "budget": 100000, "kind": "goods"},
                     "receipt": {"item": "G00001", "price": 58000, "task": "t1"},
                     "settled": {"ledger": 17, "sku": "G00001", "grade": "A", "warranty_hours": 6,
                                 "shop": "https://fixture.invalid", "claim_honest": True, "value": 100000}}

    def test_good_deal_is_four_and_only_exceptional_value_is_five(self):
        self.assertEqual(review(self.deal, terms=self.terms)["stars"], 4)
        self.deal["receipt"]["price"] = 46000
        self.assertEqual(review(self.deal, terms=self.terms)["stars"], 5)

    def test_any_actual_shortcoming_caps_review_at_two(self):
        for field, value, reason in [("grade", "B", "grade_below_requirement"),
                                     ("warranty_hours", 5, "short_warranty")]:
            with self.subTest(field=field):
                deal = {**self.deal, "settled": {**self.deal["settled"], field: value}}
                result = review(deal, terms=self.terms)
                self.assertEqual(result["stars"], 2)
                self.assertIn(reason, result["reasons"])
        self.deal["receipt"]["price"] = 60000
        result = review(self.deal, terms=self.terms)
        self.assertEqual(result["stars"], 2)
        self.assertIn("above_target_price", result["reasons"])

    def test_false_claim_or_zero_value_is_one_even_at_a_low_price(self):
        for field, value in [("claim_honest", False), ("value", 0)]:
            with self.subTest(field=field):
                deal = {**self.deal, "settled": {**self.deal["settled"], field: value}}
                self.assertEqual(review(deal, terms=self.terms)["stars"], 1)

    def test_review_never_publishes_private_budget_target_or_limit(self):
        self.deal["receipt"]["price"] = 60000
        text = review(self.deal, terms=self.terms)["review"]
        for private in (100000, self.terms["target_price"], self.terms["price_limit"]):
            self.assertNotIn(str(private), text)
        self.assertIn("60000", text)

    def test_food_assessment_identifies_review_time_not_unproven_payment_condition(self):
        order = {"round": 3, "kind": "food", "use_within_hours": 2}
        terms = buying_terms(100000, product={"list_price": 100000}, order=order)
        deal = {**self.deal, "order": {**self.deal["order"], **order},
                "settled": {**self.deal["settled"], "expires_at": 1000 + 2.5 * 3600}}
        result = review(deal, terms=terms, now=1000)
        self.assertEqual(result["stars"], 2)
        self.assertIn("at review time", result["review"])
        deal["settled"]["expires_at"] = 999
        result = review(deal, terms=terms, now=1000)
        self.assertEqual(result["stars"], 1)
        self.assertIn("does not establish its condition at payment", result["review"])

    def test_original_policy_is_matched_to_order_shop_receipt_and_task(self):
        record = {"order": "o1", "url": "https://fixture.invalid", "item": "G00001", "price": 58000,
                  "observed_receipt": self.deal["receipt"], "result": "receipt_issued", "purchase_policy": self.terms}
        self.assertEqual(purchase_terms(self.deal, [record]), self.terms)
        self.assertIsNone(purchase_terms(self.deal, [{**record, "url": "https://wrong.invalid"}]))
        self.assertIsNone(purchase_terms(self.deal, [{**record, "price": 1}]))
        self.assertIsNone(purchase_terms(self.deal, [{**record, "observed_receipt": {"task": "other"}}]))

    def test_reviews_enter_memory_only_after_verified_payment(self):
        record = {"order": "o1", "item": "G00001", "price": 58000,
                  "observed_receipt": self.deal["receipt"], "result": "receipt_issued", "stars": 3}
        self.deal["settled"]["grade"] = "B"
        row = {"order": "o1", "receipt": self.deal["receipt"], "assessment": review(self.deal, terms=self.terms)}
        self.assertEqual(attach_reviews([record], [row])[0]["stars"], 3)
        updated = attach_reviews([{**record, "result": "settled"}], [row])[0]
        self.assertEqual(updated["stars"], 2)
        self.assertIn("grade_below_requirement", updated["review_reasons"])
        self.assertEqual(record["stars"], 3)

    def test_absent_context_never_invents_a_price_complaint(self):
        result = review(self.deal)
        self.assertEqual(result["stars"], 3)
        self.assertNotIn("above_target_price", result["reasons"])
