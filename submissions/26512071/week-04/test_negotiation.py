"""Offline behavior tests for episode termination and scoring."""

import unittest

from negotiation import Scenario, run_episode


class ScriptedModel:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)

    def __call__(self, system_prompt: str,
                 messages: list[dict[str, str]]) -> str:
        return next(self.outputs)


class NegotiationTests(unittest.TestCase):
    def test_feasible_nonviolating_deal_is_correct(self):
        model = ScriptedModel([
            '{"performative":"propose","content":{"price":80}}',
            '{"performative":"accept-proposal","content":{"price":null}}',
        ])
        result = run_episode(
            "structured", Scenario("s1", "camera", 70, 100),
            model, 8, lambda _: None,
        )
        self.assertEqual(result.outcome, "deal")
        self.assertEqual(result.price, 80)
        self.assertEqual(result.correct, 1)
        self.assertEqual(result.violation, 0)

    def test_impossible_scenario_refusal_is_correct(self):
        model = ScriptedModel([
            '{"performative":"propose","content":{"price":45}}',
            '{"performative":"refuse","content":{"price":null}}',
        ])
        result = run_episode(
            "structured", Scenario("s2", "lamp", 55, 45),
            model, 8, lambda _: None,
        )
        self.assertEqual(result.outcome, "no_deal")
        self.assertEqual(result.correct, 1)

    def test_out_of_range_deal_is_a_violation(self):
        model = ScriptedModel([
            '{"performative":"propose","content":{"price":120}}',
            '{"performative":"accept-proposal","content":{"price":null}}',
        ])
        result = run_episode(
            "structured", Scenario("s3", "headphones", 90, 100),
            model, 8, lambda _: None,
        )
        self.assertEqual(result.outcome, "deal")
        self.assertEqual(result.correct, 0)
        self.assertEqual(result.violation, 1)

    def test_acceptance_without_proposal_does_not_end_episode(self):
        model = ScriptedModel([
            '{"performative":"accept-proposal","content":{"price":null}}',
            '{"performative":"refuse","content":{"price":null}}',
        ])
        result = run_episode(
            "structured", Scenario("s4", "chair", 120, 100),
            model, 2, lambda _: None,
        )
        self.assertEqual(result.outcome, "no_deal")
        self.assertIn("acceptance without", result.note)


if __name__ == "__main__":
    unittest.main()
