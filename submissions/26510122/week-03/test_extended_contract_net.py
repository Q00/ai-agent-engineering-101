"""Offline checks for semantic repair signals and reputation updates."""

import json
from pathlib import Path
import tempfile
import unittest

from extended_contract_net import parse_extended_bid
from ontology import OntologyState


ROOT = Path(__file__).resolve().parent


class ExtendedContractNetTests(unittest.TestCase):
    def test_conflicting_language_is_flagged(self):
        raw = json.dumps({
            "participate": True,
            "confidence": 95,
            "reason": "I want the task",
            "task_interpretation": "Write a notice",
            "dimensions": {
                "task_understanding": 90,
                "capability": 20,
                "expected_success": 40,
                "willingness": 100,
            },
        })
        bid = parse_extended_bid("developer", raw)
        self.assertIn("confidence_expected_success_conflict", bid.warnings)
        self.assertIn("high_confidence_low_capability", bid.warnings)

    def test_claimed_and_derived_status_remain_separate(self):
        state = OntologyState.from_file(ROOT / "ontology_seed.json")
        for index in range(3):
            state.observe_award(f"code-{index}", "developer", True, "code")
        node = state.data["contractors"]["developer"]
        self.assertEqual(node["self_view"]["claimed_status"], "specialist")
        self.assertEqual(node["manager_view"]["derived_status"], "veteran")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state.save(path)
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
