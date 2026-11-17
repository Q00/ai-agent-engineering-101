"""Dry-run assignments; fixture outcomes below are never used as market data."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricing_simulator import PricingSimulator, candidate_price


class PricingSimulatorTest(unittest.TestCase):
    def test_exact_prices_and_ceiling(self):
        self.assertEqual([candidate_price(100, arm) for arm in ("Low", "Mid", "High")], [106, 109, 112])
        self.assertEqual([candidate_price(180, arm) for arm in ("Low", "Mid", "High")], [191, 197, 202])
        self.assertEqual([candidate_price(260, arm) for arm in ("Low", "Mid", "High")], [276, 284, 292])

    def test_seed_and_per_item_blocks_are_reproducible_and_balanced(self):
        def run():
            simulator = PricingSimulator(26510122)
            assigned = [simulator.assign(f"lamp-{i}", "lamp", 100) for i in range(30)]
            assigned += [simulator.assign(f"chair-{i}", "chair", 180) for i in range(30)]
            return assigned, simulator.summary()
        first, summary = run()
        self.assertEqual(first, run()[0])
        self.assertEqual({(row["item"], row["arm"]): row["assigned"] for row in summary},
                         {(item, arm): 10 for item in ("lamp", "chair")
                          for arm in ("Low", "Mid", "High")})

    def test_duplicate_assignment_exposure_and_outcome(self):
        simulator = PricingSimulator(7)
        first = simulator.assign("task-1", "lamp", 100)
        self.assertEqual(first, simulator.assign("task-1", "lamp", 100))
        with self.assertRaises(ValueError):
            simulator.assign("task-1", "chair", 180)
        with self.assertRaises(ValueError):
            simulator.verify_actual_ask("task-1", first["planned_ask"] + 1)
        with self.assertRaises(ValueError):
            simulator.record_outcome("task-1", "COUNTED", 110)
        simulator.verify_actual_ask("task-1", first["planned_ask"])
        simulator.record_outcome("task-1", "COUNTED", 110)
        simulator.record_outcome("task-1", "COUNTED", 110)
        with self.assertRaises(ValueError):
            simulator.record_outcome("task-1", "COUNTED", 111)
        self.assertEqual(simulator.summary()[0]["counted_margin"], 10)
        self.assertEqual(simulator.summary()[0]["assigned"], 1)


if __name__ == "__main__":
    unittest.main()
