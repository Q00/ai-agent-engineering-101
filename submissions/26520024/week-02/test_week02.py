"""Offline tests; mock model replies never enter experimental results.csv."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import codex_backend as backend
import harness_plan_execute as plan
import harness_react as react
import run_ab
import tools_shared as shared


@contextlib.contextmanager
def temporary_cwd():
    old = Path.cwd()
    with tempfile.TemporaryDirectory() as folder:
        os.chdir(folder)
        try:
            yield Path(folder)
        finally:
            os.chdir(old)


def fake_model(replies):
    pending = iter(replies)

    def complete(system, history, specs, enabled, model, on_usage):
        on_usage(10, 2)
        return next(pending)
    return complete


class Week02Tests(unittest.TestCase):
    def setUp(self):
        self.provider = patch.object(shared, "PROVIDER", "codex")
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def test_input_and_regex(self):
        with temporary_cwd() as root, patch.object(shared, "WORKSPACE", root):
            (root / "app.log").write_text("10:00 ERROR a\n11:00 INFO b\n", encoding="utf-8")
            self.assertEqual(shared.count_pattern("app.log", "ERROR"), "1")
            self.assertIn("INFO", shared.read_file("app.log"))

    def test_reference_and_traversal_denied(self):
        for name in ("TASK.md", "../app.log", "/etc/passwd", "logs/react-01.txt"):
            with self.assertRaises(ValueError):
                shared.read_file(name)

    def test_symlink_denied(self):
        with temporary_cwd() as root, patch.object(shared, "WORKSPACE", root):
            (root / "answer.txt").write_text("secret", encoding="utf-8")
            (root / "app.log").symlink_to(root / "answer.txt")
            with self.assertRaises(ValueError):
                shared.read_file("app.log")

    def test_read_guard(self):
        with temporary_cwd() as root, patch.object(shared, "WORKSPACE", root):
            (root / "app.log").write_text("x" * 5000, encoding="utf-8")
            self.assertEqual(len(shared.read_file("app.log")), 4000)

    def test_transport_does_not_validate_inner_plan(self):
        text, calls = backend.parse_reply('{"text":"not a plan","tool_calls":[]}',
                                          shared.TOOL_SPECS, False)
        self.assertEqual(text, "not a plan")
        self.assertEqual(calls, [])
        self.assertIsNone(plan.parse_plan(text))

    def test_bad_tool_requests_rejected(self):
        for name, args, enabled in (("shell", "{}", True),
                                    ("read_file", "[]", True),
                                    ("read_file", "{}", False)):
            raw = json.dumps({"text": "", "tool_calls": [
                {"name": name, "arguments_json": args}]})
            with self.assertRaises(ValueError):
                backend.parse_reply(raw, shared.TOOL_SPECS, enabled)

    def test_usage_cached_tokens_not_double_counted(self):
        meter = shared.Meter()
        event = {"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 7}}
        backend.record_events(json.dumps(event), meter.record_usage)
        self.assertEqual(meter.tokens, 107)

    def test_internal_actions_and_missing_usage_rejected(self):
        cases = [{"type": "item.completed", "item": {"type": "command_execution"}},
                 {"type": "turn.failed"}, {"type": "turn.completed", "usage": {}}]
        for event in cases:
            with self.assertRaises(RuntimeError):
                backend.record_events(json.dumps(event), lambda a, b: None)

    def test_react_observation_then_finish(self):
        replies = [("Thought: read input", [{"name": "read_file", "args": {"path": "app.log"}}]),
                   ("Answer: 14:00", [])]
        with patch.object(backend, "complete", side_effect=fake_model(replies)):
            answer, meter = react.run_react("task", log=lambda x: None)
        self.assertEqual(answer, "Answer: 14:00")
        self.assertEqual((meter.tokens, meter.iters, meter.interventions), (24, 2, 0))

    def test_react_cap(self):
        replies = [("", [{"name": "read_file", "args": {"path": "app.log"}}])] * 2
        with patch.object(backend, "complete", side_effect=fake_model(replies)):
            answer, meter = react.run_react("task", max_steps=2, log=lambda x: None)
        self.assertIn("MAX_STEPS", answer)
        self.assertEqual(meter.iters, 2)

    def test_approval_and_denial_both_counted(self):
        for approved in (True, False):
            replies = [("", [{"name": "read_file", "args": {"path": "app.log"}}]), ("done", [])]
            with patch.object(backend, "complete", side_effect=fake_model(replies)), \
                 patch.object(react, "IRREVERSIBLE", {"read_file"}), \
                 patch.object(react, "ask_human", return_value=approved):
                _, meter = react.run_react("task", log=lambda x: None)
            self.assertEqual(meter.interventions, 1)

    def test_plan_parse_failure_counted(self):
        with patch.object(backend, "complete", side_effect=fake_model([("oops", [])])):
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertEqual((answer, meter.iters, replans), ("plan parse failed", 1, 0))

    def test_plan_replan_and_final_calls(self):
        replies = [('[' + '"first"' + ']', []), ("OFF_PLAN: unavailable", []),
                   ('["replacement"]', []), ("step complete", []), ("Answer: 14:00", [])]
        with patch.object(backend, "complete", side_effect=fake_model(replies)):
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertEqual((answer, meter.iters, replans), ("Answer: 14:00", 5, 1))

    def test_errors_become_tool_observations(self):
        chat = shared.Chat("system", shared.Meter())
        reply = shared.Reply("", [shared.ToolCall("x", "read_file", {"path": "TASK.md"})])
        chat.run_tools(reply, log=lambda x: None)
        self.assertTrue(chat.messages[-1]["content"].startswith("error:"))

    def test_failed_call_retains_attempt_and_marks_usage_unknown(self):
        meter = shared.Meter()
        with patch.object(backend, "complete", side_effect=RuntimeError("offline failure")):
            with self.assertRaises(RuntimeError):
                shared.Chat("system", meter).send()
        self.assertEqual(meter.iters, 1)
        self.assertFalse(meter.tokens_complete)

    def test_runner_records_crash_and_does_not_overwrite(self):
        def crash(task, log, meter):
            meter.iters = 2
            meter.tokens = 12
            meter.tokens_complete = False
            print("partial output")
            raise RuntimeError("offline failure")
        with temporary_cwd() as root, contextlib.redirect_stdout(io.StringIO()):
            (root / "logs").mkdir()
            row = run_ab.run_one(1, "react", crash, "task", "14:00", {})
            self.assertEqual(row[2:6], ["X", "", 2, 0])
            self.assertIn("partial output", (root / "logs/react-01.txt").read_text())
            self.assertEqual(run_ab.next_run_number(), 2)
            with self.assertRaises(FileExistsError):
                run_ab.run_one(1, "react", crash, "task", "14:00", {})

    def test_fixed_criterion(self):
        self.assertTrue(run_ab.judge("Answer: 14:00", "14:00"))
        self.assertFalse(run_ab.judge("Answer: 13:00", "14:00"))
        self.assertIsNone(plan.parse_plan('{"steps": []}'))
        self.assertEqual(plan.parse_plan('```json\n["read"]\n```'), ["read"])


if __name__ == "__main__":
    unittest.main()
