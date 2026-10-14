"""Promotion failure cases use explicitly local fixtures, never model calls."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy import StrategyPolicy
from promote_policy import verify_adversarial, verify_holdout, verify_test_coverage, verify_trace


class PromotionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.policy = StrategyPolicy()

    def identity(self):
        return {"policy_version": self.policy.version, "policy_sha256": self.policy.sha256}

    def test_passing_flag_alone_cannot_replace_holdout_cases(self):
        report = {**self.identity(), "mode": "actual_harness", "independent": True,
                  "evaluation_kind": "single_turn_holdout", "passed": True, "cases": []}
        with self.assertRaisesRegex(ValueError, "four current"):
            verify_holdout(report, self.policy)

    def test_synthetic_or_different_policy_cannot_supply_actual_holdout(self):
        report = {**self.identity(), "mode": "synthetic_surrogate", "independent": True,
                  "evaluation_kind": "single_turn_holdout", "passed": True, "cases": []}
        with self.assertRaisesRegex(ValueError, "actual-harness"):
            verify_holdout(report, self.policy)
        report["policy_sha256"] = "wrong candidate"
        with self.assertRaisesRegex(ValueError, "exact candidate"):
            verify_holdout(report, self.policy)

    def test_partial_local_guard_suite_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test_guard_fixture.py"
            source.write_text("class GuardTests:\n    def test_budget(self): pass\n    def test_duplicate(self): pass\n")
            group = {"mode": "deterministic_guard", "passed": True, "cases": [
                {"name": "test_guard_fixture.GuardTests.test_budget", "mode": "deterministic_guard", "passed": True}]}
            with self.assertRaisesRegex(ValueError, "Partial test coverage"):
                verify_test_coverage(group, source, "deterministic_guard")
            group["cases"].append({"name": "test_guard_fixture.GuardTests.test_duplicate",
                                   "mode": "deterministic_guard", "passed": True, "skipped": True})
            with self.assertRaisesRegex(ValueError, "skipped"):
                verify_test_coverage(group, source, "deterministic_guard")

    def test_adversarial_stub_report_cannot_be_presented_as_complete(self):
        report = {**self.identity(), "passed": False,
                  "adversarial_tests": {"mode": "actual_harness", "passed": False, "skipped": True, "cases": []}}
        with self.assertRaisesRegex(ValueError, "pass every group"):
            verify_adversarial(report, self.policy)

    def test_invented_success_event_without_cli_call_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake-trace.jsonl"
            path.write_text(json.dumps({"run_id": "local-fixture", "role": "buyer",
                                        "event": "harness_decision", "decision": {"action": "accept", "price": 200}}) + "\n")
            with self.assertRaisesRegex(ValueError, "successful actual CLI call"):
                verify_trace(path, role="buyer", policy=self.policy)

    def test_stub_harness_command_is_rejected_even_if_decision_is_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stub-trace.jsonl"
            fixture = {"run_id": "local-fixture", "role": "buyer", "event": "harness_request",
                       "model": "fixture", "strategy": "balanced", "context": {"budget": 210, "ask": 200},
                       "command": ["python", "stub.py", "--output-schema", "schema.json", "--output-last-message", "out.json"]}
            path.write_text(json.dumps(fixture) + "\n")
            with self.assertRaisesRegex(ValueError, "real structured Codex"):
                verify_trace(path, role="buyer", policy=self.policy)


if __name__ == "__main__":
    unittest.main()
