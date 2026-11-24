"""Week 13 tests. Run: python -m unittest -v test_week13 (from this folder)."""
import json
import os
import tempfile
import unittest

import mock_payment
import contract


def agree():
    return contract.negotiate(item="우유 190mL x 24", qty=1, list_price=12000,
                              seller_floor=9000, buyer_budget=10000)


class Negotiation(unittest.TestCase):
    def test_agreement_lands_between_floor_and_budget(self):
        n = agree()
        self.assertEqual(n["agreement"]["unit_price"], 10000)
        self.assertEqual(n["agreement"]["total"], 10000)
        self.assertEqual(n["messages"][-1]["act"], "accept")

    def test_no_agreement_when_budget_is_under_the_floor(self):
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=20000,
                               seller_floor=15000, buyer_budget=14000)
        self.assertIsNone(n["agreement"])
        self.assertEqual(n["messages"][-1]["act"], "walk_away")

    def test_total_is_unit_price_times_quantity(self):
        n = contract.negotiate(item="생수 2L x 6", qty=3, list_price=12000,
                               seller_floor=9000, buyer_budget=10000)
        self.assertEqual(n["agreement"]["total"], 30000)


class Contract(unittest.TestCase):
    def test_sealed_wrapper_is_what_the_payment_layer_accepts(self):
        w = contract.seal(agree(), contract_id="ctr-test-1", created_at=1795520000)
        self.assertEqual(w["body"]["schema"], "meshkit.transaction-contract/v1")
        self.assertEqual(w["hash"], mock_payment.digest(w["body"]))
        self.assertEqual(w["body"]["offer"]["total"], 10000)
        self.assertEqual(w["body"]["parties"]["seller"], "nocha12")
        receipt = mock_payment.settle_and_receipt(mock_payment.Ledger(), w)
        self.assertEqual(receipt["contract_id"], "ctr-test-1")

    def test_both_parties_tags_verify_and_a_changed_body_does_not(self):
        w = contract.seal(agree(), contract_id="ctr-test-1", created_at=1795520000)
        self.assertEqual(contract.check_wrapper(w), [])
        w["body"]["offer"]["total"] = 1
        self.assertNotEqual(contract.check_wrapper(w), [])

    def test_no_contract_without_an_agreement(self):
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=20000,
                               seller_floor=15000, buyer_budget=14000)
        with self.assertRaises(ValueError):
            contract.seal(n, contract_id="ctr-x", created_at=1)


if __name__ == "__main__":
    unittest.main()
