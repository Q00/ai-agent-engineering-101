import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import codex_backend
from contract_net import (HEADER, choose_winner, load_json, parse_bid, run_round,
                          system_prompt)
from run_experiment import execute_run, read_rows


def bid(score=90, yes=True):
    return dict(bid=yes, confidence=score, reason="synthetic offline test only")


class ContractNetTests(unittest.TestCase):
    def setUp(self):
        self.prompts = load_json("prompts.json")
        self.tasks = load_json("tasks.json")

    def test_valid_bid(self):
        self.assertEqual(parse_bid(json.dumps(bid()))[0], bid())

    def test_valid_decline(self):
        self.assertEqual(parse_bid(json.dumps(bid(0, False)))[0], bid(0, False))

    def test_fractional_confidence(self):
        self.assertEqual(parse_bid(json.dumps(bid(95.5)))[0]["confidence"], 95.5)

    def test_bad_json(self):
        self.assertIsNone(parse_bid("not JSON")[0])

    def test_fences_are_not_repaired(self):
        self.assertIsNone(parse_bid("```json\n" + json.dumps(bid()) + "\n```")[0])

    def test_invalid_types_ranges_and_keys(self):
        bad = [[], None, dict(bid(), extra=1), dict(bid(), bid="true"),
               dict(bid(), confidence=True), dict(bid(), confidence=-1),
               dict(bid(), confidence=101), dict(bid(), confidence="90"),
               dict(bid(), reason="  "), dict(bid(), reason=4)]
        for obj in bad:
            with self.subTest(obj=obj):
                self.assertIsNone(parse_bid(json.dumps(obj))[0])

    def test_nonfinite_and_duplicate_keys(self):
        for score in (float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(parse_bid(json.dumps(bid(score)))[0])
        self.assertIsNone(parse_bid('{"bid":true,"bid":false,"confidence":90,"reason":"x"}')[0])

    def test_highest_wins(self):
        self.assertEqual(choose_winner([("A", bid(90)), ("C", bid(95))]), "C")

    def test_tie_first_respondent(self):
        self.assertEqual(choose_winner([("B", bid()), ("A", bid())]), "B")

    def test_declines_and_invalid_cannot_win(self):
        self.assertIsNone(choose_winner([("A", None), ("B", bid(100, False))]))

    def test_homogeneous_only_changes_skill(self):
        for name in ("A", "B", "C"):
            base = system_prompt("baseline", name, self.prompts)
            expected = base.replace(self.prompts["skills"][name], self.prompts["generalist"])
            self.assertEqual(system_prompt("homogeneous", name, self.prompts), expected)

    def test_overconfident_only_appends_to_c(self):
        for name in ("A", "B", "C"):
            base = system_prompt("baseline", name, self.prompts)
            self.assertEqual(system_prompt("overconfident", name, self.prompts),
                             base + (self.prompts["overconfident"] if name == "C" else ""))

    def test_balanced_gold(self):
        self.assertEqual([t["gold"] for t in self.tasks].count("A"), 2)
        self.assertEqual([t["gold"] for t in self.tasks].count("B"), 2)
        self.assertEqual([t["gold"] for t in self.tasks].count("C"), 2)
        self.assertEqual(len({t["id"] for t in self.tasks}), 6)

    def round(self, raws, tasks=None):
        replies = iter(raws)
        self.events = []
        self.inputs = []
        def call(system, user, emit):
            self.inputs.append((system, user))
            return next(replies), dict(input_tokens=10, output_tokens=2)
        def emit(event, **data):
            self.events.append(dict(event=event, **data))
        return run_round(tasks or self.tasks[:1], "baseline", self.prompts, call, emit)

    def test_message_count_excludes_declines(self):
        counts, stats = self.round([json.dumps(bid()), json.dumps(bid(0, False)), "bad"])
        self.assertEqual(counts, dict(tasks=1, correct=1, messages=5, unassigned=0, misawards=0))
        self.assertEqual(stats, dict(calls=3, parse_fails=1, declines=1,
                                    input_tokens=30, output_tokens=6))

    def test_unassigned(self):
        counts, _ = self.round(["bad"] * 3)
        self.assertEqual(counts, dict(tasks=1, correct=0, messages=3, unassigned=1, misawards=0))

    def test_misaward(self):
        counts, _ = self.round([json.dumps(bid(s)) for s in (80, 90, 99)])
        self.assertEqual(counts["misawards"], 1)
        self.assertEqual(counts["messages"], 7)

    def test_gold_not_sent_to_model_or_awarder(self):
        task = dict(self.tasks[0], gold="HIDDEN_EVALUATOR_ONLY")
        self.round([json.dumps(bid())] * 3, [task])
        self.assertNotIn(task["gold"], json.dumps(self.inputs))
        award = next(e for e in self.events if e["event"] == "award")
        self.assertEqual(award["contractor"], "A")
        self.assertNotIn("gold", award)

    def test_changing_gold_does_not_change_inputs_or_winner(self):
        self.round([json.dumps(bid())] * 3)
        inputs = self.inputs
        self.round([json.dumps(bid())] * 3, [dict(self.tasks[0], gold="C")])
        self.assertEqual(inputs, self.inputs)
        self.assertEqual(next(e for e in self.events if e["event"] == "award")["contractor"], "A")

    def test_crash_row_blank_and_log_preserved(self):
        def crash(*args):
            raise RuntimeError("offline synthetic failure")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory)
            failure = execute_run(path, "001", "baseline", self.tasks,
                                  self.prompts, {}, crash)
            self.assertIsInstance(failure, RuntimeError)
            row = read_rows(path)[0]
            self.assertEqual(list(row), list(HEADER))
            self.assertTrue(all(row[k] == "" for k in HEADER[2:7]))
            self.assertIn("offline synthetic failure", row["note"])
            before = (path / "logs/001-baseline.log").read_bytes()
            with self.assertRaises(FileExistsError):
                execute_run(path, "001", "baseline", self.tasks, self.prompts, {}, crash)
            self.assertEqual(before, (path / "logs/001-baseline.log").read_bytes())

    def test_csv_appends(self):
        def call(*args):
            return json.dumps(bid()), dict(input_tokens=1, output_tokens=1)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory)
            for index in ("001", "002"):
                execute_run(path, index, "baseline", self.tasks, self.prompts, {}, call)
            self.assertEqual(len(read_rows(path)), 2)

    def test_raw_events_validate(self):
        events = [dict(type="item.completed", item=dict(type="agent_message", text="{}")),
                  dict(type="turn.completed", usage=dict(input_tokens=20, output_tokens=3))]
        usage, text = codex_backend.inspect_events("\n".join(map(json.dumps, events)))
        self.assertEqual(text, "{}")
        self.assertEqual(usage["input_tokens"], 20)

    def test_native_actions_are_rejected(self):
        for kind in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            with self.assertRaises(RuntimeError):
                codex_backend.inspect_events(json.dumps(dict(type="item.started", item=dict(type=kind))))

    def test_missing_or_failed_turn_rejected(self):
        for raw in ("", '{"type":"error"}', '{"type":"turn.failed"}',
                    '{"type":"turn.completed","usage":{}}'):
            with self.assertRaises(RuntimeError):
                codex_backend.inspect_events(raw)


if __name__ == "__main__":
    unittest.main()
