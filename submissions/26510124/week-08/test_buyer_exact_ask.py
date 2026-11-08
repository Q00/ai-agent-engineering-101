import unittest
from unittest.mock import Mock

from market_buyer import Buyer, BuyerError, PolicyRefusal


class ExactAskTests(unittest.TestCase):
    def buyer(self, enabled=True):
        buyer = Buyer(Mock(), url="https://shop.invalid", item="G1", budget=100,
                      policy={"price_limit": 90, "max_concession": 10, "max_decisions": 2,
                              "allow_accept_above_concession": enabled})
        buyer.authorized_price = 40
        return buyer

    def test_offer_label_at_exact_ask_has_same_numeric_wire_authorization_as_accept(self):
        for action in ("offer", "accept"):
            self.buyer().validate_decision({"action": action, "price": 80, "reason": "agreed"}, "Asking price: 80")

    def test_unknown_ambiguous_different_ask_disabled_and_hard_cap_remain_blocked(self):
        for text, price, enabled in (("no quote", 80, True), ("List price: 100; asking price: 80", 80, True),
                                     ("asking 81", 80, True), ("asking 80", 80, False), ("asking 91", 91, True)):
            with self.subTest(text=text, price=price, enabled=enabled), self.assertRaises(PolicyRefusal):
                self.buyer(enabled).validate_decision({"action": "offer", "price": price, "reason": "test"}, text)

    def test_invalid_accept_price_still_fails(self):
        with self.assertRaises(BuyerError):
            self.buyer().validate_decision({"action": "accept", "price": 80, "reason": "test"}, "asking 81")


if __name__ == "__main__":
    unittest.main()
