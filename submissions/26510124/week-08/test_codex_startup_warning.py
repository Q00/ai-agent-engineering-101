"""Replay the observed Codex startup diagnostic without permitting tool use.

All CLI/auth calls are mocked. The warning text is copied from the supplied
server evidence, independently of the implementation's allowlisted constant.
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import model_backend as backend

ROOT = Path(__file__).resolve().parent
WARNING = (
    "Code Mode is unavailable because code-mode host is disabled. "
    "Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`."
)
ANSWER = '{"reason":"ok","ready":true}'


class CodexStartupWarningTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "model-profile.json").write_text('{"version":1,"trading":"sol","advisor":"haiku"}')
        (self.root / "advisor-config.json").write_text('{"enabled":false}')
        (self.root / "answer.json").write_text(ANSWER)

    @staticmethod
    def events():
        return [
            {"type": "thread.started", "thread_id": "fixture-thread"},
            {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": WARNING}},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": ANSWER}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}},
        ]

    @staticmethod
    def result(events, code=0):
        return subprocess.CompletedProcess([], code, "\n".join(json.dumps(row) for row in events), "")

    def test_observed_startup_warning_and_successful_decision_are_accepted(self):
        for profile in ("sol", "astra", "luna"):
            with self.subTest(profile=profile):
                self.assertEqual(backend.answer(profile, self.result(self.events()), self.root), ANSWER)

    def test_preflight_preserves_original_warning_and_keeps_code_mode_disabled(self):
        def execute(command, **kwargs):
            self.assertEqual(command[command.index("--model") + 1], "gpt-6.1-sol")
            for feature in ("code_mode", "code_mode_host", "shell_tool", "multi_agent"):
                self.assertTrue(any(command[index:index + 2] == ["--disable", feature]
                                    for index in range(len(command) - 1)))
            Path(command[command.index("--output-last-message") + 1]).write_text(ANSWER)
            return self.result(self.events())

        output = io.StringIO()
        with patch.object(backend, "ROOT", self.root), \
                patch.object(backend.shutil, "which", return_value="/fixture/codex"), \
                patch.object(backend, "ensure_chatgpt") as auth, \
                patch.object(backend, "execute", side_effect=execute) as run, redirect_stdout(output):
            self.assertEqual(backend.main(["--preflight"]), 0)
        auth.assert_called_once()
        run.assert_called_once()
        self.assertIn("gpt-6.1-sol low", output.getvalue())
        logs = list((self.root / "logs").glob("model-preflight-*.log"))
        self.assertEqual(len(logs), 1)
        saved = logs[0].read_text()
        self.assertIn(self.result(self.events()).stdout, saved)
        self.assertIn("FINAL ANSWER:\n" + ANSWER, saved)

    def test_same_warning_after_turn_start_or_completion_is_rejected(self):
        for after_completion in (False, True):
            events = self.events()
            warning = events.pop(1)
            events.insert(len(events) if after_completion else 2, warning)
            with self.subTest(after_completion=after_completion), self.assertRaisesRegex(ValueError, "item error"):
                backend.answer("sol", self.result(events), self.root)

    def test_other_or_near_match_item_errors_are_rejected_without_message_content(self):
        for message in ("fixture-sensitive-auth-error", WARNING + " ", WARNING.lower(), WARNING[:-1], None):
            events = self.events()
            events[1]["item"]["message"] = message
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, "item error") as error:
                backend.answer("sol", self.result(events), self.root)
            if isinstance(message, str):
                self.assertNotIn(message, str(error.exception))

    def test_warning_is_not_allowed_in_started_or_updated_item_events(self):
        for kind in ("item.started", "item.updated"):
            events = self.events()
            events[1]["type"] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "item error"):
                backend.answer("sol", self.result(events), self.root)

    def test_actual_tools_still_fail_before_or_after_startup_warning(self):
        for item_type in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            for insertion in (1, 3):
                events = self.events()
                events.insert(insertion, {"type": "item.completed", "item": {"type": item_type}})
                with self.subTest(item_type=item_type, insertion=insertion), self.assertRaisesRegex(ValueError, "tool"):
                    backend.answer("sol", self.result(events), self.root)

    def test_startup_warning_does_not_hide_failed_turn_or_missing_completion(self):
        base = self.events()
        cases = [base[:-1]]
        for kind in ("error", "turn.failed"):
            events = deepcopy(base)
            events.insert(3, {"type": kind, "message": "fixture failure"})
            cases.append(events)
        for events in cases:
            with self.subTest(events=events), self.assertRaises(ValueError):
                backend.answer("sol", self.result(events), self.root)

    def test_success_with_warning_still_requires_bounded_regular_answer_file(self):
        answer = self.root / "answer.json"
        answer.unlink()
        with self.assertRaises(ValueError):
            backend.answer("sol", self.result(self.events()), self.root)
        answer.write_text("x" * 65537)
        with self.assertRaises(ValueError):
            backend.answer("sol", self.result(self.events()), self.root)
        answer.unlink()
        target = self.root / "actual-answer.json"
        target.write_text(ANSWER)
        answer.symlink_to(target)
        with self.assertRaises(ValueError):
            backend.answer("sol", self.result(self.events()), self.root)


if __name__ == "__main__":
    unittest.main()
