"""A canary must be stable, sparse, and stay inside the existing price range."""

from __future__ import annotations

import unittest
from collections import Counter

from pricing_experiment import assign_price
from pricing_simulator import candidate_price


class PricingExperimentTest(unittest.TestCase):
    def test_default_is_unchanged_and_assignment_is_stable(self):
        self.assertEqual(assign_price("a", "lamp", 100, 0), ("legacy_cap_1.12", None))
        self.assertEqual(assign_price("a", "lamp", 100, 5), assign_price("a", "lamp", 100, 5))
        with self.assertRaises(ValueError):
            assign_price("a", "lamp", 100, 101)

    def test_canary_is_small_and_uses_exact_candidate_prices(self):
        assignments = [assign_price(str(i), "lamp", 100, 5) for i in range(1000)]
        counts = Counter(arm for arm, _ in assignments)
        self.assertGreaterEqual(counts["legacy_cap_1.12"], 900)
        self.assertGreater(counts["Low"], 0)
        self.assertGreater(counts["Mid"], 0)
        self.assertGreater(counts["High"], 0)
        for arm, ask in assignments:
            if ask is not None:
                self.assertEqual(ask, candidate_price(100, arm))
                self.assertGreaterEqual(ask, 100)
                self.assertLessEqual(ask, 112)


if __name__ == "__main__":
    unittest.main()
