"""Training-only simulator v2 checks; no runtime model or policy is installed."""
from contextlib import redirect_stderr
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy import ACTIONS, STRATEGIES
import train_policy
from train_policy import ENVIRONMENT_VERSION, REWARD_VERSION, Scenario, initial_action, simulate


class TrainingV2Tests(unittest.TestCase):
    def test_patient_buyer_can_improve_an_already_affordable_quote(self):
        scenario = Scenario("buyer", 100, 120, 110, 60, 0, False, False, "steady")
        direct, balanced, patient = [simulate(scenario, strategy) for strategy in ("direct", "balanced", "patient")]
        self.assertEqual([result["reason"] for result in (direct, balanced, patient)], ["deal"] * 3)
        self.assertLess(balanced["price"], direct["price"])
        self.assertLess(patient["price"], direct["price"])
        self.assertGreater(patient["calls"], direct["calls"])
        self.assertGreater(patient["reward"], direct["reward"])

    def test_patient_seller_can_improve_an_already_above_reserve_bid(self):
        scenario = Scenario("seller", 100, 130, 140, 105, 0, False, False, "steady")
        direct, patient = simulate(scenario, "direct"), simulate(scenario, "patient")
        self.assertEqual(direct["reason"], "deal")
        self.assertEqual(patient["reason"], "deal")
        self.assertGreater(patient["price"], direct["price"])
        self.assertGreater(patient["calls"], direct["calls"])
        self.assertGreater(patient["reward"], direct["reward"])

    def test_impatient_counterpart_can_walk_away_from_a_feasible_probe(self):
        for scenario in (Scenario("buyer", 100, 120, 110, 60, 0, False, False, "impatient"),
                         Scenario("seller", 100, 130, 140, 105, 0, False, False, "impatient")):
            with self.subTest(role=scenario.role):
                direct, patient = simulate(scenario, "direct"), simulate(scenario, "patient")
                self.assertEqual(direct["reason"], "deal")
                self.assertEqual(patient["reason"], "no_deal")
                self.assertTrue(patient["feasible"])
                self.assertEqual(patient["termination"], "counterpart_walked_away")
                self.assertGreater(direct["reward"], patient["reward"])

    def test_initial_actions_ignore_opponent_limits_and_patience(self):
        buyer = Scenario("buyer", 100, 120, 110, 60, 0, False, False, "steady")
        seller = Scenario("seller", 100, 130, 140, 105, 0, False, False, "steady")
        for scenario, changed, injected in (
            (buyer, replace(buyer, reserve=1, counterpart="impatient"), {"reserve": 999}),
            (seller, replace(seller, budget=999, counterpart="impatient"), {"budget": 1}),
        ):
            for strategy in ACTIONS:
                with self.subTest(role=scenario.role, strategy=strategy):
                    observed = scenario.observation()
                    expected = initial_action(scenario.role, observed, strategy)
                    self.assertEqual(expected, initial_action(changed.role, changed.observation(), strategy))
                    self.assertEqual(expected, initial_action(scenario.role, {**observed, **injected}, strategy))
        self.assertNotIn("reserve", buyer.observation())
        self.assertNotIn("budget", seller.observation())

    def test_known_last_own_round_can_close_without_hidden_patience(self):
        for scenario in (Scenario("buyer", 100, 120, 110, 60, 8, False, False, "steady"),
                         Scenario("seller", 100, 130, 140, 105, 8, False, False, "steady")):
            with self.subTest(role=scenario.role):
                action = initial_action(scenario.role, scenario.observation(), "patient")
                self.assertEqual(action["action"], "accept")
                self.assertEqual(simulate(scenario, "patient")["reason"], "deal")

    def test_price_improvement_outweighs_one_extra_call_in_the_required_tradeoff(self):
        buyer = Scenario("buyer", 100, 120, 130, 80, 0, False, False, "steady")
        seller = replace(buyer, role="seller")
        buyer_dear = train_policy._outcome(buyer, "deal", calls=1, price=105)
        buyer_cheap = train_policy._outcome(buyer, "deal", calls=2, price=100)
        self.assertGreater(buyer_cheap["reward"], buyer_dear["reward"])
        seller_cheap = train_policy._outcome(seller, "deal", calls=1, price=105)
        seller_dear = train_policy._outcome(seller, "deal", calls=2, price=110)
        self.assertGreater(seller_dear["reward"], seller_cheap["reward"])
        failed_probe = train_policy._outcome(buyer, "no_deal", calls=2, price=None)
        self.assertGreater(buyer_dear["reward"], failed_probe["reward"])

    def test_v2_counteroffers_preserve_guards_for_all_strategies_and_fraud_fixtures(self):
        rng = random.Random(7301)
        for _ in range(250):
            scenario = train_policy.sample_scenario(rng)
            for strategy in ACTIONS:
                result = simulate(scenario, strategy)
                self.assertEqual(result["environment_version"], ENVIRONMENT_VERSION)
                self.assertFalse(result["executed_constraint_violation"])
                if result["price"] is not None:
                    self.assertEqual(result["reason"], "deal")
                    self.assertFalse(scenario.fraud)
                    self.assertGreaterEqual(result["price"], scenario.reserve)
                    self.assertLessEqual(result["price"], scenario.budget)
                if scenario.fraud:
                    self.assertIsNone(result["price"])

    def test_fixture_training_versions_metadata_and_uses_a_separate_holdout_rng(self):
        active = Path(train_policy.__file__).parent / "policies/active.json"
        before = active.read_bytes() if active.exists() else None
        with patch.object(train_policy, "sample_scenario", wraps=train_policy.sample_scenario) as sample:
            candidate, report = train_policy.train(seed=7301, episodes=100, holdout_episodes=20)
        self.assertEqual(len(sample.call_args_list), 120)
        training_rng = sample.call_args_list[0].args[0]
        holdout_rng = sample.call_args_list[100].args[0]
        self.assertIsNot(training_rng, holdout_rng)
        self.assertTrue(all(call.args[0] is training_rng for call in sample.call_args_list[:100]))
        self.assertTrue(all(call.args[0] is holdout_rng for call in sample.call_args_list[100:]))
        self.assertNotEqual(report["seed"], report["holdout_seed"])
        self.assertEqual(candidate["metadata"]["holdout_seed"], report["holdout_seed"])
        self.assertIn("env2", candidate["version"])
        self.assertEqual(candidate["metadata"]["environment_version"], ENVIRONMENT_VERSION)
        self.assertEqual(report["reward_version"], REWARD_VERSION)
        self.assertEqual(candidate["metadata"]["strategy_guidance_sha256"],
                         hashlib.sha256(json.dumps(STRATEGIES, sort_keys=True).encode()).hexdigest())
        self.assertTrue(candidate["metadata"]["toy_assumptions"])
        self.assertFalse(report["promoted"])
        self.assertFalse(report["actual_harness_evaluated"])
        self.assertEqual(active.read_bytes() if active.exists() else None, before)

    def test_cli_refuses_existing_artifacts_before_any_training_or_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for existing_name in ("candidate.json", "report.json"):
                with self.subTest(existing=existing_name):
                    output, report = root / "candidate.json", root / "report.json"
                    existing = root / existing_name
                    existing.write_text("TEST ONLY original attempt must survive")
                    argv = ["train_policy.py", "--output", str(output), "--report", str(report)]
                    with patch.object(sys, "argv", argv), patch.object(train_policy, "train") as trainer, \
                            redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        train_policy.main()
                    trainer.assert_not_called()
                    self.assertEqual(existing.read_text(), "TEST ONLY original attempt must survive")
                    self.assertFalse((report if existing == output else output).exists())
                    existing.unlink()


if __name__ == "__main__":
    unittest.main()
