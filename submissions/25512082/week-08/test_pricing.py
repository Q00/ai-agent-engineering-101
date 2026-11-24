from __future__ import annotations

import unittest

from pricing import fallback_decision
from protocol import RESERVES


class FallbackDecisionTests(unittest.TestCase):
    def test_offer_near_the_ask_is_accepted(self):
        self.assertEqual(fallback_decision("lamp", 105, 100, 1), {"action": "accept", "price": 100})

    def test_low_offer_gets_a_counter_between_ask_and_reserve(self):
        self.assertEqual(fallback_decision("chair", 225, 150, 1), {"action": "counter", "price": 205})

    def test_never_accepts_below_the_reserve(self):
        for item, reserve in RESERVES.items():
            for rounds in range(6):
                decision = fallback_decision(item, reserve + 40, reserve - 1, rounds)
                self.assertEqual(decision["action"], "counter")
                self.assertGreaterEqual(decision["price"], reserve)

    def test_reserve_offer_is_accepted_after_three_offers(self):
        self.assertEqual(fallback_decision("bookshelf", 295, 260, 3), {"action": "accept", "price": 260})

    def test_counter_always_moves_below_the_current_ask(self):
        self.assertEqual(fallback_decision("bookshelf", 270, 200, 1), {"action": "counter", "price": 265})


if __name__ == "__main__":
    unittest.main()
