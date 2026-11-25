"""negotiate_pro: GPT Pro's counter formulas for pricing.json negotiate.formula (메인 11/07 03:22)."""
import unittest
from unittest import mock

import negotiate
import negotiate_pro

DEAL = {"ask": 30000, "reserve": 20000, "open": 32000, "rounds": 1,
        "history": [("you asked", 32000), ("buyer offered", 21000), ("you asked", 30000)]}


def state(rounds=1, offer=22000, history=None, floor=26000):
    d = dict(DEAL, rounds=rounds, history=DEAL["history"] if history is None else history)
    return dict(d, floor=floor, offer=offer, round=rounds)


class FittedCounterTest(unittest.TestCase):
    def test_the_share_by_round_is_the_fitted_median(self):
        for r, share in [(1, 0.6285), (2, 0.5124), (3, 0.4610)]:
            s = state(rounds=r, history=[("you asked", 32000)])   # no earlier offer: the "all" row
            self.assertAlmostEqual(negotiate_pro.fitted_counter(s), 26000 + share * 4000, delta=1)

    def test_from_round_four_the_rising_buyer_gets_less(self):
        rising = state(rounds=5, offer=23000)   # 23000 > the earlier 21000
        flat = state(rounds=5, offer=23000, history=[("you asked", 32000)])
        self.assertAlmostEqual(negotiate_pro.fitted_counter(rising), 26000 + 0.8752 * 4000, delta=1)
        self.assertAlmostEqual(negotiate_pro.fitted_counter(flat), 26000 + 0.7600 * 4000, delta=1)

    def test_the_current_offer_already_in_history_is_not_its_own_previous(self):
        h = DEAL["history"] + [("buyer offered", 23000)]
        self.assertEqual(negotiate_pro.fitted_counter(state(rounds=5, offer=23000, history=h)),
                         negotiate_pro.fitted_counter(state(rounds=5, offer=23000)))

    def test_through_the_swap_it_stays_inside_floor_and_ask(self):
        with mock.patch("shop_memory.load_pricing", return_value={"negotiate": {"formula": "negotiate_pro:fitted_counter"}}):
            p = negotiate.counter_price(dict(DEAL, rounds=2), 26000, 22000)
            self.assertEqual(p, round(26000 + 0.5124 * 4000))
            self.assertEqual(negotiate.counter_price(dict(DEAL, ask=26000), 26000, 22000), 26000)


if __name__ == "__main__":
    unittest.main()
