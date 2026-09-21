"""Offline tests: no SDK client is constructed and no API is called."""

import unittest

from contract_net import (
    BASELINE_SKILLS,
    GENERALIST_SKILL,
    OVERCONFIDENT_INSTRUCTION,
    make_team,
    parse_bid,
    run_contract_net,
)


class QueueModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def __call__(self, _system, _user):
        return next(self.responses)


class ContractNetTests(unittest.TestCase):
    def test_conditions_change_only_intended_fields(self):
        baseline = make_team("baseline")
        homogeneous = make_team("homogeneous")
        overconfident = make_team("overconfident")

        self.assertEqual([c.skill for c in baseline], list(BASELINE_SKILLS.values()))
        self.assertEqual([c.skill for c in homogeneous], [GENERALIST_SKILL] * 3)
        self.assertEqual([c.skill for c in overconfident], list(BASELINE_SKILLS.values()))
        self.assertEqual(overconfident[0].extra_instruction, "")
        self.assertEqual(overconfident[1].extra_instruction, "")
        self.assertEqual(overconfident[2].extra_instruction, OVERCONFIDENT_INSTRUCTION)

    def test_strict_parser_accepts_exact_object(self):
        bid = parse_bid('{"bid": true, "confidence": 95, "reason": "matched skill"}')
        self.assertTrue(bid.bid)
        self.assertEqual(bid.confidence, 95)

    def test_strict_parser_rejects_prose_fence_and_wrong_schema(self):
        invalid = [
            'Here is my bid: {"bid": true, "confidence": 90, "reason": "x"}',
            '```json\n{"bid": true, "confidence": 90, "reason": "x"}\n```',
            '{"bid": "true", "confidence": 90, "reason": "x"}',
            '{"bid": true, "confidence": 101, "reason": "x"}',
            '{"bid": true, "confidence": 90, "reason": "x", "extra": 1}',
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_bid(raw)

    def test_manager_tie_message_count_and_parse_failure(self):
        task = [{"id": 1, "desc": "A math task", "gold": "A"}]
        responses = [
            '{"bid": true, "confidence": 90, "reason": "math"}',
            '{"bid": true, "confidence": 90, "reason": "general"}',
            "I can explain this, but this is not JSON.",
        ]
        lines = []
        metrics = run_contract_net(
            task, "baseline", QueueModel(responses), lines.append
        )

        self.assertEqual(metrics.tasks, 1)
        self.assertEqual(metrics.correct, 1)  # A wins the A/B confidence tie.
        self.assertEqual(metrics.messages, 6)  # 3 announcements + 2 bids + 1 award.
        self.assertEqual(metrics.unassigned, 0)
        self.assertEqual(metrics.misawards, 0)
        self.assertEqual(metrics.parse_fails, 1)
        self.assertIn("I can explain this, but this is not JSON.", "\n".join(lines))

    def test_no_valid_true_bid_is_unassigned(self):
        task = [{"id": 1, "desc": "A math task", "gold": "A"}]
        false_bid = '{"bid": false, "confidence": 10, "reason": "not my skill"}'
        metrics = run_contract_net(
            task, "baseline", QueueModel([false_bid, false_bid, false_bid]), lambda _: None
        )
        self.assertEqual(metrics.messages, 3)
        self.assertEqual(metrics.unassigned, 1)
        self.assertEqual(metrics.correct, 0)
        self.assertEqual(metrics.misawards, 0)


if __name__ == "__main__":
    unittest.main()
