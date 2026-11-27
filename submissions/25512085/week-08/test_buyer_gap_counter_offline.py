"""Synthetic Round 3 counters through the buyer, under the guarded offline runner."""
import unittest
from unittest.mock import patch

import market_buyer as buyer
import test_buyer_price_offline as price_tests


class BuyerGapCounterTests(unittest.TestCase):
    setUp = price_tests.BuyerPriceTests.setUp
    run_buyer = price_tests.BuyerPriceTests.run_buyer
    receipt = price_tests.BuyerPriceTests.receipt

    def quotes(self, asks, budget=120000, complete_after=0):
        # int(list_price * .58) = 60000; food cap = 93104.
        self.catalog[self.sku]["list_price"] = 103449
        index = [0]

        def quote(*args):
            value = asks[min(index[0], len(asks) - 1)]
            index[0] += 1
            return value

        receipt = self.receipt(asks[-1]) if complete_after else None
        with patch.object(buyer, "extract_last_number", side_effect=quote):
            return self.run_buyer(ask=asks[0], budget=budget, receipt=receipt,
                                  complete_after=complete_after)

    def test_second_counter_uses_previous_offer_and_current_ask(self):
        _, offers, _, _ = self.quotes([90000, 80000])
        self.assertEqual(offers[:3], [60000, 67000, 80000])

    def test_nonbinding_budget_changes_do_not_change_counter(self):
        for budget in (95000, 120000, 1000000):
            with self.subTest(budget=budget):
                _, offers, _, _ = self.quotes([90000, 80000], budget)
                self.assertEqual(offers[:2], [60000, 67000])

    def test_initial_over_cap_and_current_actual_ask_not_preclamped(self):
        _, offers, _, _ = self.quotes([100000, 100000])
        self.assertEqual(offers, [60000, 74000, 93104])

    def test_counter_uses_previous_offer_not_reserve_after_low_opening(self):
        _, offers, _, _ = self.quotes([100000, 80000], budget=70000)
        self.assertEqual(offers, [50400, 60760, 70000])

    def test_goods_use_the_same_gap_rule(self):
        self.order["kind"] = "goods"
        _, offers, _, _ = self.quotes([90000, 80000])
        # Opening ask now exceeds .80 goods cap: existing .72 opening anchor.
        # Then 59586 + half-up(.35 * (80000 - 59586)) = 66731.
        self.assertEqual(offers[:3], [59586, 66731, 80000])

    def test_binding_policy_and_budget_caps(self):
        for budget, cap in ((120000, 93104), (85000, 85000)):
            with self.subTest(budget=budget):
                _, offers, _, _ = self.quotes([100000, 200000], budget)
                self.assertEqual(offers[:2], [60000, cap])
                self.assertTrue(all(0 < offer <= cap for offer in offers))

    def test_equal_or_lower_ask_never_raises_offer(self):
        for ask in (60000, 59999, 50000):
            with self.subTest(ask=ask):
                _, offers, _, _ = self.quotes([90000, ask])
                self.assertEqual(offers[:2], [60000, ask])

    def test_integer_gap_half_up_and_minimum_one(self):
        for gap, increment in ((1, 1), (2, 1), (3, 1), (4, 1),
                               (5, 2), (10, 4), (30, 11)):
            with self.subTest(gap=gap):
                _, offers, _, _ = self.quotes([90000, 60000 + gap])
                self.assertEqual(offers[:2], [60000, 60000 + increment])

    def test_cheap_opening_still_accepts_immediately(self):
        result, offers, _, _ = self.quotes([60000], complete_after=1)
        self.assertEqual(offers, [60000])
        self.assertEqual(result["receipt"]["price"], 60000)

    def test_third_and_final_acceptance_unchanged(self):
        result, offers, _, _ = self.quotes([90000, 80000], complete_after=3)
        self.assertEqual(offers, [60000, 67000, 80000])
        self.assertEqual(result["receipt"]["price"], 80000)
        result, offers, _, _ = self.quotes([100000, 100000, 100000, 80000],
                                          complete_after=4)
        self.assertEqual(offers, [60000, 74000, 93104, 80000])
        self.assertEqual(result["receipt"]["price"], 80000)

    def test_legacy_counter_sequence_unchanged(self):
        result, offers, _, _ = self.run_buyer(
            ask=200, receipt=dict(self.receipt(200), item="lamp"),
            complete_after=3, budget=250, legacy=True)
        self.assertEqual(offers, [100, 155, 200])
        self.assertEqual(result["receipt"]["price"], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
