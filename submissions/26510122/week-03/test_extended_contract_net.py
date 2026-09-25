"""Offline checks for semantic repair signals and reputation updates."""

import json
from pathlib import Path
import tempfile
import unittest

from extended_contract_net import (
    ExtendedBid,
    add_profile_warnings,
    choose_extended_winner,
    parse_extended_bid,
)
from identity_state import IdentityState


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
        state = IdentityState.from_file(ROOT / "identity_seed.json")
        for index in range(3):
            state.observe_award(
                f"code-{index}", "developer", True, ["python", "debugging"],
                90, 90,
            )
        node = state.data["contractors"]["developer"]
        self.assertEqual(node["self_view"]["claimed_status"], "specialist")
        self.assertEqual(node["manager_view"]["derived_status"], "veteran")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state.save(path)
            self.assertTrue(path.is_file())

    def test_high_capability_needs_profile_support(self):
        state = IdentityState.from_file(ROOT / "identity_seed.json")
        raw = json.dumps({
            "participate": True,
            "confidence": 90,
            "reason": "I can do it",
            "task_interpretation": "Debug Python",
            "dimensions": {
                "task_understanding": 90,
                "capability": 90,
                "expected_success": 90,
                "willingness": 90,
            },
        })
        bid = parse_extended_bid("writer", raw)
        bid = add_profile_warnings(
            bid, {"required_capabilities": ["python", "debugging"]}, state
        )
        self.assertIn("capability_not_supported_by_profile", bid.warnings)

    def test_trajectory_calibrates_repeated_overconfidence(self):
        state = IdentityState.from_file(ROOT / "identity_seed.json")
        for index in range(3):
            state.observe_award(
                f"write-{index}", "developer", False, ["technical-writing"],
                95, 95,
            )
        view = state.data["contractors"]["developer"]["manager_view"]
        self.assertEqual(
            view["domain_reliability"]["technical-writing"]["reliability"], 0
        )
        self.assertEqual(view["calibration"]["mean_absolute_gap"], 95)
        self.assertEqual(view["recent_success_rate"], 0)

        bid = ExtendedBid(
            "developer", True, 95, "I can write it", "Write a notice",
            {
                "task_understanding": 95,
                "capability": 95,
                "expected_success": 95,
                "willingness": 95,
            },
            "{}",
        )
        winner, scores = choose_extended_winner(
            [bid], state, ["technical-writing"]
        )
        self.assertIsNotNone(winner)
        self.assertLess(scores["developer"]["final"], 50)
        self.assertEqual(scores["developer"]["evidence_source"], "domain")


if __name__ == "__main__":
    unittest.main()
