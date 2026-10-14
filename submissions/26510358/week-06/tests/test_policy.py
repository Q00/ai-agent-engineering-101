"""Meaningful policy checks; synthetic fixtures never impersonate run reports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy import ACTIONS, StrategyPolicy, context_key, promote_policy, strategy_instruction
from train_policy import Scenario, sample_scenario, simulate, train


class PolicyTests(unittest.TestCase):
    def test_real_parameter_updates_are_reproducible(self):
        candidate, report = train(seed=19, episodes=700, holdout_episodes=120)
        same_candidate, same_report = train(seed=19, episodes=700, holdout_episodes=120)
        self.assertEqual(candidate, same_candidate)
        self.assertEqual(report, same_report)
        self.assertEqual(report["parameter_updates"], 700)
        visits = candidate["metadata"]["state_action_visits"]
        self.assertEqual(sum(sum(row.values()) for row in visits.values()), 700)
        self.assertTrue(any(value != 0 for row in candidate["q_values"].values() for value in row.values()))
        self.assertNotEqual(report["seed"], report["holdout_seed"])
        self.assertFalse(report["actual_harness_evaluated"])
        self.assertFalse(report["promoted"])

    def test_buyer_encoder_does_not_use_private_reserve(self):
        public = {"round": 2, "budget": 120, "ask": 130, "flags": []}
        self.assertEqual(context_key("buyer", public), context_key("buyer", {**public, "reserve": 999999}))
        scenario = Scenario("buyer", 100, 120, 130, 60, 0, False, False, "steady")
        self.assertNotIn("reserve", scenario.observation())
        self.assertNotIn("budget", Scenario("seller", 100, 120, 130, 60, 0, False, False, "steady").observation())

    def test_training_baseline_ignores_runtime_override(self):
        _, ordinary = train(seed=9, episodes=100, holdout_episodes=40)
        with patch.dict("os.environ", {"A2A_POLICY_PATH": "/does-not-exist-policy.json"}):
            _, overridden = train(seed=9, episodes=100, holdout_episodes=40)
        self.assertEqual(ordinary["baseline"], overridden["baseline"])

    def test_runtime_policy_is_frozen_for_current_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            context = {"round": 0, "budget": 120, "ask": 130}
            key = context_key("buyer", context)
            payload = {"schema_version": 1, "version": "fixture-v1",
                       "q_values": {key: {"balanced": 0, "patient": 2, "direct": 1}},
                       "metadata": {"nested": {"value": 1}}}
            path.write_text(json.dumps(payload))
            frozen = StrategyPolicy(path)
            payload["q_values"][key]["direct"] = 5
            payload["version"] = "fixture-v2"
            path.write_text(json.dumps(payload))
            self.assertEqual(frozen.choose("buyer", context), "patient")
            self.assertEqual(frozen.version, "fixture-v1")
            self.assertEqual(StrategyPolicy(path).choose("buyer", context), "direct")
            metadata = frozen.metadata
            metadata["nested"]["value"] = 9
            self.assertEqual(frozen.metadata["nested"]["value"], 1)

    def test_environment_source_is_explicit_and_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation-only.json"
            context = {"round": 0, "budget": 120, "ask": 130}
            key = context_key("buyer", context)
            payload = {"schema_version": 1, "version": "evaluation-fixture",
                       "q_values": {key: {"balanced": 0, "patient": 2, "direct": 1}}, "metadata": {}}
            path.write_text(json.dumps(payload))
            with patch.dict("os.environ", {"A2A_POLICY_PATH": str(path)}):
                frozen = StrategyPolicy()
                payload["q_values"][key]["direct"] = 9
                path.write_text(json.dumps(payload))
                self.assertEqual(frozen.choose("buyer", context), "patient")
                self.assertEqual(StrategyPolicy().choose("buyer", context), "direct")

    def test_reward_distinguishes_legitimate_from_false_walkaway(self):
        feasible = Scenario("buyer", 100, 120, 165, 80, 7, False, False, "stubborn")
        impossible = Scenario("buyer", 100, 75, 165, 50, 7, False, False, "stubborn")
        valid = simulate(feasible, "direct")
        false_walkaway = simulate(feasible, "patient")
        legitimate = simulate(impossible, "patient")
        self.assertEqual(valid["reason"], "deal")
        self.assertEqual(false_walkaway["reason"], "no_deal")
        self.assertGreater(valid["reward"], legitimate["reward"])
        self.assertGreater(legitimate["reward"], false_walkaway["reward"])

    def test_all_simulated_strategies_respect_execution_guards(self):
        rng = random.Random(55)
        for _ in range(200):
            scenario = sample_scenario(rng)
            for action in ACTIONS:
                result = simulate(scenario, action)
                self.assertFalse(result["executed_constraint_violation"])
                if result["price"] is not None:
                    self.assertGreaterEqual(result["price"], scenario.reserve)
                    self.assertLessEqual(result["price"], scenario.budget)
                    self.assertFalse(scenario.fraud)

    def test_synthetic_holdout_cannot_promote_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            candidate, metrics = train(seed=3, episodes=100, holdout_episodes=20)
            candidate_path = base / "candidate.json"
            candidate_path.write_text(json.dumps(candidate))
            digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
            evidence = {"policy_sha256": digest, "policy_version": candidate["version"],
                        "actual_harness_holdout": {"mode": metrics["mode"], "independent": True,
                                                   "passed": True, "cases": [{"passed": True}]}}
            validation = base / "validation.json"
            validation.write_text(json.dumps(evidence))
            trade = base / "trade-fixture.json"
            trade.write_text("{}")
            target = base / "active.json"
            target.write_text("existing policy")
            with self.assertRaisesRegex(ValueError, "actual-harness"):
                promote_policy(candidate_path, target, validation, trade)
            self.assertEqual(target.read_text(), "existing policy")
            evidence["actual_harness_holdout"]["mode"] = "actual_harness"
            evidence["guard_tests"] = {"passed": False, "cases": [{"passed": False}]}
            validation.write_text(json.dumps(evidence))
            with self.assertRaisesRegex(ValueError, "guard_tests"):
                promote_policy(candidate_path, target, validation, trade)
            self.assertEqual(target.read_text(), "existing policy")

    def test_default_is_untrained_and_guidance_does_not_return_prices(self):
        with patch.dict("os.environ", {}, clear=True), patch("policy.Path.is_file", return_value=False):
            baseline = StrategyPolicy()
        self.assertEqual(baseline.version, "baseline-v1")
        self.assertEqual(baseline.choose("buyer", {"budget": 120, "ask": 130}), "balanced")
        self.assertFalse(baseline.metadata["synthetic"])
        self.assertIn("budget", strategy_instruction("buyer", "patient"))
        self.assertIn("reserve", strategy_instruction("seller", "balanced"))
        with self.assertRaises(ValueError):
            strategy_instruction("seller", "invented")

    def test_corrupted_numeric_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({"schema_version": 1, "version": "invalid",
                                       "q_values": {"buyer|early|near|clear": {"balanced": float("nan"), "patient": 1, "direct": 2}}}))
            with self.assertRaisesRegex(ValueError, "finite"):
                StrategyPolicy(path)


if __name__ == "__main__":
    unittest.main()
