"""Offline tests only. Scripted responses are NOT model behavior observations."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import codex_backend
import first_agent as agent


def tool(name, inputs):
    return {"action": "tool", "tool": name, "arguments": inputs, "answer": ""}


def final(text="Done."):
    return {"action": "final", "tool": "none", "arguments": {}, "answer": text}


class AgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.workspace = self.parent / "submission"
        self.workspace.mkdir()
        self.enterContext(patch.object(agent, "WORKSPACE", self.workspace))
        (self.workspace / "notes.txt").write_text(
            "baseline: 1.25\nadapter: 2.50\nevaluation: 0.75\n", encoding="utf-8"
        )

    def run_scripted(self, responses, **kwargs):
        pending = iter(responses)
        self.requests = []
        def choose(goal, tools, history, model):
            self.requests.append(copy.deepcopy({
                "goal": goal, "tools": tools, "history": history, "model": model,
            }))
            return next(pending)

        with patch.object(agent, "choose_action", side_effect=choose):
            with patch.object(agent, "backend_info", return_value={
                    "cli_version": "scripted", "authentication": "offline-test"}):
                with redirect_stdout(io.StringIO()):
                    return agent.run(agent.DEFAULT_GOAL, **kwargs)

    def test_calculator_runtime_total_and_arithmetic(self):
        self.assertEqual(agent.calculator("1.25 + 2.50 + 0.75"), "4.5")
        self.assertEqual(agent.calculator("3 * (4 + 5) / 9"), "3.0")
        self.assertEqual(agent.calculator("-2 ** 3 + +1"), "-7")

    def test_calculator_rejects_code_and_unbounded_inputs(self):
        for expression in ("__import__('os')", "'text'", "True", "2 ** 100000",
                           "1e309", "1e12 * 2", "(-1) ** 0.5", "1+" * 101,
                           "+".join(["1"] * 30)):
            with self.subTest(expression=expression):
                with self.assertRaises((ValueError, SyntaxError)):
                    agent.calculator(expression)
        with self.assertRaises(ZeroDivisionError):
            agent.calculator("1 / 0")

    def test_read_is_complete_or_rejected(self):
        self.assertIn("baseline: 1.25", agent.read_file("notes.txt"))
        (self.workspace / "large.txt").write_text("x" * 4001, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no partial data"):
            agent.read_file("large.txt")

    def test_read_rejects_traversal_sibling_prefix_and_symlinks(self):
        outside = self.parent / "submission-other"
        outside.mkdir()
        (outside / "note.txt").write_text("outside", encoding="utf-8")
        (self.workspace / "linked.txt").symlink_to(outside / "note.txt")
        for path in ("../submission-other/note.txt", "linked.txt",
                     str(outside / "note.txt"), "first_agent.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    agent.read_file(path)

    def test_write_appends_and_can_be_read_back(self):
        agent.write_note("outputs/summary.txt", "GPU hours: 4.5")
        agent.write_note("outputs/summary.txt", "Second note\n")
        self.assertEqual(agent.read_file("outputs/summary.txt"),
                         "GPU hours: 4.5\nSecond note\n")

    def test_write_rejects_non_output_paths_and_bad_content(self):
        original = agent.read_file("notes.txt")
        for path in ("notes.txt", "outputs/../notes.txt", "logs/fake.txt",
                     "../outside.txt", "outputs/script.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    agent.write_note(path, "do not write")
        for content in ("", " ", "x" * 4001, 123):
            with self.subTest(content_type=type(content).__name__):
                with self.assertRaises(ValueError):
                    agent.write_note("outputs/summary.txt", content)
        self.assertEqual(agent.read_file("notes.txt"), original)

    def test_write_rejects_symlinks_outside_outputs(self):
        (self.workspace / "outputs").symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            agent.write_note("outputs/escaped.txt", "do not write")
        self.assertFalse((self.parent / "escaped.txt").exists())

    def test_scripted_three_tool_feedback_and_saved_result(self):
        answer = self.run_scripted([
            tool("read_file", {"path": "notes.txt"}),
            tool("calculator", {"expression": "1.25 + 2.50 + 0.75"}),
            tool("write_note", {"path": "outputs/summary.txt",
                                "content": "Total GPU hours: 4.5"}),
            final("Saved 4.5 GPU hours."),
        ])
        self.assertEqual(answer, "Saved 4.5 GPU hours.")
        self.assertEqual(agent.read_file("outputs/summary.txt"),
                         "Total GPU hours: 4.5\n")
        self.assertEqual(len(self.requests), 4)
        self.assertIn("baseline: 1.25",
                      self.requests[1]["history"][-1]["content"]["output"])
        result = self.requests[2]["history"][-1]["content"]
        self.assertEqual(result["name"], "calculator")
        self.assertEqual(result["output"], "4.5")
        self.assertFalse(result["is_error"])

    def test_two_tool_mode_cannot_dispatch_write_note(self):
        self.run_scripted([
            tool("write_note", {"path": "outputs/not-written.txt",
                                "content": "must be denied"}),
            final("No write tool is available."),
        ], tool_count=2)
        names = [entry["name"] for entry in self.requests[0]["tools"]]
        self.assertEqual(names, ["calculator", "read_file"])
        self.assertTrue(self.requests[1]["history"][-1]["content"]["is_error"])
        self.assertFalse((self.workspace / "outputs").exists())

    def test_tool_errors_are_returned_and_recovery_is_possible(self):
        self.run_scripted([
            tool("read_file", {"path": "missing.txt"}),
            tool("calculator", {"expression": "1 / 0"}),
            tool("calculator", {"expression": "1 + 2"}),
            final("3"),
        ])
        failures = [request["history"][-1]["content"]
                    for request in self.requests[1:3]]
        self.assertEqual([item["name"] for item in failures], ["read_file", "calculator"])
        self.assertTrue(all(item["is_error"] for item in failures))
        self.assertFalse(self.requests[3]["history"][-1]["content"]["is_error"])

    def test_step_limit_stops_before_another_model_request(self):
        with self.assertRaisesRegex(RuntimeError, "max steps exceeded"):
            self.run_scripted([
                tool("read_file", {"path": "notes.txt"}),
            ], max_steps=1)
        self.assertEqual(len(self.requests), 1)

    def test_incomplete_or_empty_responses_are_failures(self):
        for reply in ({"action": "invalid"}, final("")):
            with self.subTest(reply=reply):
                with self.assertRaises(RuntimeError):
                    self.run_scripted([reply])

    def test_missing_codex_has_an_actionable_error(self):
        with patch.object(codex_backend.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Codex CLI not found"):
                codex_backend.executable()

    def test_decision_parser_accepts_structured_tool_arguments(self):
        raw = json.dumps({"action": "tool", "tool": "read_file",
                          "arguments_json": '{"path":"notes.txt"}', "answer": ""})
        self.assertEqual(codex_backend.parse_decision(raw, agent.TOOLS),
                         tool("read_file", {"path": "notes.txt"}))

    def test_decision_parser_rejects_malformed_or_unavailable_actions(self):
        valid = {"action": "tool", "tool": "read_file",
                 "arguments_json": '{"path":"notes.txt"}', "answer": ""}
        invalid = [
            {}, {**valid, "arguments_json": "[]"}, {**valid, "tool": "write_note"},
            {**valid, "action": "unknown"}, {**valid, "answer": "premature answer"},
            {"action": "final", "tool": "none", "arguments_json": "{}", "answer": ""},
        ]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    codex_backend.parse_decision(json.dumps(item), agent.TOOLS[:2])

    def test_backend_disables_shell_and_rejects_internal_actions(self):
        from types import SimpleNamespace

        captured = []
        def run(command, **kwargs):
            captured.append(command)
            event = {"type": "item.completed",
                     "item": {"type": "command_execution", "command": "forbidden"}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(event), stderr="")

        with patch.object(codex_backend, "executable", return_value="/fake/codex"):
            with patch.object(codex_backend.subprocess, "run", side_effect=run):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "internal action"):
                        codex_backend.choose_action("test", agent.TOOLS, [], "test")
        command = captured[0]
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("shell_tool", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn('web_search="disabled"', command)


if __name__ == "__main__":
    print("OFFLINE TESTS: scripted responses; no live model or network calls.",
          flush=True)
    unittest.main(verbosity=2)
