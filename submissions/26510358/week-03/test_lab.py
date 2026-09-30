"""Offline behavioral checks; synthetic bids never enter the experiment results."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from contractor import OVERCONFIDENT, make_contractors, parse_bid
from manager import run_round
from model import Meter
from run import HEADER, load_tasks, next_run_id, run_one


def response(bid=True, confidence=90, reason="test response"):
    return json.dumps(dict(bid=bid, confidence=confidence, reason=reason))


class ContractNetTests(unittest.TestCase):
    def round(self, replies, gold="B", team=None):
        events, inputs = [], []
        replies = iter(replies)
        def model(system, user, meter, log):
            inputs.append((system, user))
            return next(replies)
        result = run_round([dict(id=1, desc="a task", gold=gold)],
                           team or make_contractors("baseline"), Meter(), model,
                           lambda event, **data: events.append((event, data)))
        return result, events, inputs

    def test_highest_valid_bid_and_message_count(self):
        result, _, inputs = self.round([response(False, 100), response(True, 85), response(True, 80)])
        self.assertEqual((result.correct, result.messages, result.misawards), (1, 6, 0))
        self.assertEqual(len(inputs), 3)
        self.assertTrue(all("gold" not in user for _, user in inputs))

    def test_tie_uses_response_order_even_if_names_reverse(self):
        result, events, _ = self.round([response(True, 95)] * 3, gold="C",
                                      team=list(reversed(make_contractors("baseline"))))
        self.assertEqual(result.correct, 1)
        self.assertEqual(events[-1][1]["winner"], "C")
        self.assertEqual(result.messages, 7)

    def test_unassigned_is_not_misaward_and_parse_failure_is_not_bid(self):
        result, _, _ = self.round(["not JSON", response(False), response(False)])
        self.assertEqual((result.messages, result.unassigned, result.misawards,
                          result.correct, result.parse_fails), (3, 1, 0, 0, 1))

    def test_wrong_award_is_counted(self):
        result, _, _ = self.round([response(True, 99), response(True, 90), response(False)])
        self.assertEqual((result.correct, result.misawards, result.messages), (0, 1, 6))

    def test_strict_json_and_schema(self):
        invalid = ["[]", "null", "{}", "```json\n" + response() + "\n```",
                   response(bid="true"), response(confidence=True), response(confidence="95"),
                   response(confidence=-1), response(confidence=101), response(reason=" "),
                   response(confidence=float("nan")), response(confidence=float("inf")),
                   '{"bid":true,"bid":false,"confidence":90,"reason":"x"}',
                   response() + " trailing text"]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_bid(raw))
        for confidence in (0, 95.5, 100):
            self.assertIsNotNone(parse_bid(response(confidence=confidence)))

    def test_only_intended_condition_differences(self):
        baseline = make_contractors("baseline")
        over = make_contractors("overconfident")
        self.assertEqual(baseline[:2], over[:2])
        self.assertEqual(baseline[2].system + OVERCONFIDENT, over[2].system)
        for original, generalist in zip(baseline, make_contractors("homogeneous")):
            self.assertEqual(original.system.replace(original.skill, "general problem solving"),
                             generalist.system)

    def test_tasks_balanced_and_complete(self):
        tasks = load_tasks(Path(__file__).with_name("tasks.json"))
        self.assertEqual([sum(t["gold"] == name for t in tasks) for name in "ABC"], [2, 2, 2])

    def test_run_ids_preserve_orphan_logs(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "overconfident-12.txt").touch()
            self.assertEqual(next_run_id([{"run": "2"}, {"run": "9"}], path), 13)

    def test_crash_and_interrupt_preserve_log_and_blank_counts(self):
        for failure in (RuntimeError("connection failed"), KeyboardInterrupt("interrupted")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as root:
                path = Path(root)
                (path / "logs").mkdir()
                def model(*args):
                    raise failure
                with (path / "results.csv").open("w+", newline="") as results:
                    csv.DictWriter(results, fieldnames=HEADER).writeheader()
                    with contextlib.redirect_stdout(io.StringIO()):
                        if isinstance(failure, KeyboardInterrupt):
                            with self.assertRaises(KeyboardInterrupt):
                                run_one("baseline", 1, [dict(id=1, desc="test", gold="A")],
                                        {"task_sha256": "test"}, model, path, results)
                        else:
                            self.assertFalse(run_one("baseline", 1, [dict(id=1, desc="test", gold="A")],
                                                     {"task_sha256": "test"}, model, path, results))
                    results.seek(0)
                    row = next(csv.DictReader(results))
                self.assertTrue(all(row[key] == "" for key in HEADER[2:7]))
                self.assertEqual(json.loads(row["note"])["status"], "crashed")
                self.assertIn("[error]", (path / "logs/baseline-01.txt").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
