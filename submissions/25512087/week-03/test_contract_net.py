import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from contract_net import Contractor, build_team, choose_winner, parse_bid, run_round
from run_experiment import read_setting


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, system_prompt, announcement):
        self.calls.append((system_prompt, announcement))
        return next(self.responses)


def response(bid=True, confidence=80, reason="test"):
    return json.dumps({"bid": bid, "confidence": confidence, "reason": reason})


class BidTests(unittest.TestCase):
    def test_parse_bid_accepts_valid_json(self):
        self.assertEqual(parse_bid(response(confidence=91)), {"bid": True, "confidence": 91, "reason": "test"})

    def test_parse_bid_rejects_invalid_shape_and_range(self):
        invalid = [
            "not json",
            "[]",
            '{"bid": "yes", "confidence": 90, "reason": "x"}',
            '{"bid": true, "confidence": 101, "reason": "x"}',
            '{"bid": true, "confidence": true, "reason": "x"}',
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_bid(raw)

    def test_choose_winner_uses_confidence_then_response_order(self):
        bids = [
            (Contractor("A", "math"), parse_bid(response(confidence=90))),
            (Contractor("B", "writing"), parse_bid(response(confidence=90))),
        ]
        self.assertEqual(choose_winner(bids).name, "A")
        self.assertIsNone(choose_winner([(bids[0][0], parse_bid(response(False, 99)))]))


class ConditionTests(unittest.TestCase):
    def test_only_the_documented_condition_variable_changes(self):
        baseline = build_team("baseline")
        homogeneous = build_team("homogeneous")
        overconfident = build_team("overconfident")
        self.assertEqual([c.name for c in baseline], ["A", "B", "C"])
        self.assertEqual(len({c.skill for c in homogeneous}), 1)
        self.assertEqual([(c.name, c.skill) for c in baseline], [(c.name, c.skill) for c in overconfident])
        self.assertFalse(any(c.overconfident for c in baseline))
        self.assertEqual([c.name for c in overconfident if c.overconfident], ["C"])


class RoundTests(unittest.TestCase):
    def test_round_counts_messages_awards_and_parse_failures(self):
        tasks = [
            {"id": 1, "desc": "math", "gold": "A"},
            {"id": 2, "desc": "writing", "gold": "B"},
        ]
        model = ScriptedModel([
            response(True, 95), response(False, 20), response(False, 10),
            "invalid", response(True, 90), response(True, 80),
        ])
        events = []
        result = run_round(tasks, build_team("baseline"), model, events.append)
        self.assertEqual(result.tasks, 2)
        self.assertEqual(result.correct, 2)
        self.assertEqual(result.messages, 11)
        self.assertEqual(result.unassigned, 0)
        self.assertEqual(result.misawards, 0)
        self.assertEqual(result.parse_fails, 1)
        self.assertEqual(sum(e["event"] == "announcement" for e in events), 6)
        self.assertEqual(sum(e["event"] == "award" for e in events), 2)

    def test_round_records_unassigned_and_misaward(self):
        tasks = [
            {"id": 1, "desc": "math", "gold": "A"},
            {"id": 2, "desc": "writing", "gold": "B"},
        ]
        model = ScriptedModel([
            response(False, 0), response(False, 0), response(False, 0),
            response(False, 0), response(False, 0), response(True, 99),
        ])
        result = run_round(tasks, build_team("baseline"), model, lambda event: None)
        self.assertEqual(result.correct, 0)
        self.assertEqual(result.messages, 8)
        self.assertEqual(result.unassigned, 1)
        self.assertEqual(result.misawards, 1)

    def test_log_writer_preserves_every_event(self):
        from run_experiment import JsonlLogger

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            with JsonlLogger(path) as logger:
                logger({"event": "announcement", "task": 1})
                logger({"event": "bid", "contractor": "A"})
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([line["event"] for line in lines], ["announcement", "bid"])


class RunnerTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_BASE_URL": "https://openrouter.ai/api/v1\n"})
    def test_environment_settings_strip_accidental_newlines(self):
        self.assertEqual(
            read_setting("OPENAI_BASE_URL"),
            "https://openrouter.ai/api/v1",
        )


if __name__ == "__main__":
    unittest.main()
