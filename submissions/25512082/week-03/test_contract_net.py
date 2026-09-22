"""Offline tests: no SDK client is constructed and no API is called."""

import csv
import os
import tempfile
import unittest
from pathlib import Path

from contract_net import (
    BASELINE_SKILLS,
    GENERALIST_SKILL,
    OVERCONFIDENT_INSTRUCTION,
    make_team,
    parse_bid,
    run_contract_net,
)
from run_experiment import (
    HEADER,
    is_rate_limit_error,
    last_recorded_run,
    next_log_path,
    safe_text,
    write_log,
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

    def test_existing_results_are_preserved_and_next_run_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            with path.open("w", encoding="utf-8", newline="") as result_file:
                writer = csv.writer(result_file)
                writer.writerow(HEADER)
                writer.writerow([1, "baseline", "", "", "", "", "", "crash"])
                writer.writerow([9, "overconfident", "", "", "", "", "", "crash"])
            before = path.read_bytes()
            self.assertEqual(last_recorded_run(path), 9)
            self.assertEqual(path.read_bytes(), before)

    def test_log_path_and_exclusive_write_never_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            existing = log_dir / "baseline-01.txt"
            existing.write_text("old evidence\n", encoding="utf-8")
            new_path = next_log_path(log_dir, "baseline")
            self.assertEqual(new_path.name, "baseline-02.txt")
            write_log(new_path, ["new evidence"])
            self.assertEqual(existing.read_text(encoding="utf-8"), "old evidence\n")
            with self.assertRaises(FileExistsError):
                write_log(new_path, ["overwrite attempt"])

    def test_safe_text_redacts_only_configured_secret(self):
        previous = os.environ.get("OPENAI_API_KEY")
        try:
            os.environ["OPENAI_API_KEY"] = "test-secret-value"
            self.assertEqual(
                safe_text("failure for test-secret-value"),
                "failure for [REDACTED]",
            )
        finally:
            if previous is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous

    def test_rate_limit_detection_uses_status_code(self):
        class RateLimited(Exception):
            status_code = 429

        class OtherError(Exception):
            status_code = 500

        self.assertTrue(is_rate_limit_error(RateLimited()))
        self.assertFalse(is_rate_limit_error(OtherError()))


if __name__ == "__main__":
    unittest.main()
