import base64
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_policy import buying_terms, checkout_policy
from harness import HarnessError, buyer_decision
from market_buyer import Buyer, PolicyRefusal, ReceiptMismatch
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
