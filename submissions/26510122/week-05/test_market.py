from __future__ import annotations

import unittest

from run import verdict


class VerdictTest(unittest.TestCase):
    def test_valid_deal_is_correct(self):
        scenario = {"reserve": 80, "budget": 120}
        self.assertEqual(verdict(scenario, {"status": "deal", "deal_price": 100}), (1, 0))

    def test_deal_outside_limit_is_violation(self):
        scenario = {"reserve": 80, "budget": 120}
        self.assertEqual(verdict(scenario, {"status": "deal", "deal_price": 130}), (0, 1))

    def test_impossible_scenario_requires_no_deal(self):
        scenario = {"reserve": 150, "budget": 130}
        self.assertEqual(verdict(scenario, {"status": "no_deal", "deal_price": None}), (1, 0))
        self.assertEqual(verdict(scenario, {"status": "open", "deal_price": None}), (0, 0))


if __name__ == "__main__":
    unittest.main()
