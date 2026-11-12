"""Meaningful policy checks; synthetic fixtures never impersonate run reports."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy import StrategyPolicy, context_key, strategy_instruction


class PolicyTests(unittest.TestCase):
    def test_buyer_encoder_does_not_use_private_reserve(self):
        public = {"round": 2, "budget": 120, "ask": 130, "flags": []}
        self.assertEqual(context_key("buyer", public), context_key("buyer", {**public, "reserve": 999999}))

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
