import unittest
from settlement_memory import reconcile


class SettlementMemory(unittest.TestCase):
    def setUp(self):
        self.trade = {"order": "o1", "outcome": "purchased", "url_at_time": "https://shop.test",
                      "item": "G1", "price": 100}
        self.payment = {"order": "o1", "shop": "https://shop.test", "item": "G1", "price": 100,
                        "paid": True, "ledger": 10}

    def test_agreement_is_not_payment(self):
        result = reconcile([self.trade], {})[0]
        self.assertEqual(result["outcome"], "awaiting_payment")
        self.assertEqual(self.trade["outcome"], "purchased")

    def test_refusal_preserves_reason(self):
        payment = {**self.payment, "paid": False, "reason": "certificate missing"}
        result = reconcile([self.trade], {"o1": payment})[0]
        self.assertEqual(result["outcome"], "payment_refused")
        self.assertEqual(result["payment_reason"], "certificate missing")

    def test_only_matching_settlement_is_success(self):
        result = reconcile([self.trade], {"o1": self.payment})[0]
        self.assertEqual(result["verification"], "market_settled")
        result = reconcile([self.trade], {"o1": {**self.payment, "price": 200}})[0]
        self.assertEqual(result["outcome"], "payment_record_mismatch")


if __name__ == "__main__":
    unittest.main()
