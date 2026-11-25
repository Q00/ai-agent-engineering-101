"""Pinned Opus CLI routing and preflight, with no live login/model/network calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import harness
import model_backend as backend

ROOT = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"


class OpusModelTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "model-profile.json").write_text(json.dumps(
            {"version": 1, "trading": "opus5.5", "advisor": "luna"}))
        (self.root / "advisor-config.json").write_text('{"enabled":false}')

    def envelope(self, text='{"ready":true,"reason":"ok"}'):
        return {"type": "result", "subtype": "success", "is_error": False,
                "num_turns": 1, "permission_denials": [], "result": text,
                "modelUsage": {MODEL: {"outputTokens": 14, "canonicalModel": MODEL}}}

    def result(self, envelope=None, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(
            self.envelope() if envelope is None else envelope), "")

    def test_both_trading_roles_use_exact_opus_profile_and_keep_advisor_disabled(self):
        self.assertEqual(backend.required_executables(self.root), ("claude",))
        for prompt in ("seller_inventory.txt", "buyer.txt", "market_select.txt", "seller_opening.txt"):
            self.assertEqual(backend.for_prompt(prompt, self.root), "opus5.5")
        with self.assertRaisesRegex(ValueError, "disabled"):
            backend.for_prompt("sales_advisor.txt", self.root)

    def test_command_pins_model_low_effort_and_preserves_tool_isolation(self):
        command = backend.command("opus5.5", self.root, "buyer.txt")
        for flag, value in (("--model", MODEL), ("--effort", "low"),
                            ("--output-format", "json"), ("--tools", ""), ("--setting-sources", "")):
            self.assertEqual(command[command.index(flag) + 1], value)
        for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, command)
        self.assertNotIn("--fallback-model", command)
        self.assertNotIn("--json-schema", command)
        self.assertEqual(backend.command("haiku", self.root, "buyer.txt"), backend.CLAUDE_COMMAND)

    def test_success_returns_only_decision_text_after_model_verification(self):
        text = '{"action":"accept","price":10,"reason":"within policy"}'
        self.assertEqual(backend.answer("opus5.5", self.result(self.envelope(text)), self.root), text)
        without_canonical = self.envelope(text)
        without_canonical["modelUsage"][MODEL].pop("canonicalModel")
        self.assertEqual(backend.answer("opus5.5", self.result(without_canonical), self.root), text)

    def test_wrong_missing_or_mixed_model_never_silently_falls_back(self):
        wrong_canonical = {MODEL: {"outputTokens": 14, "canonicalModel": "claude-opus-4-6"}}
        for usage in (None, {}, {"opus": {"outputTokens": 14}},
                      {"claude-opus-4-6": {"outputTokens": 14}}, wrong_canonical,
                      {MODEL: {"outputTokens": 14}, "claude-haiku-4-5": {"outputTokens": 1}},
                      {MODEL: {"outputTokens": True}}, {MODEL: {"outputTokens": 0}}):
            with self.subTest(usage=usage):
                output = {**self.envelope(), "modelUsage": usage}
                with self.assertRaisesRegex(ValueError, "exact requested"):
                    backend.answer("opus5.5", self.result(output), self.root)

    def test_error_truncated_tools_and_malformed_results_are_rejected(self):
        for change in ({"is_error": True}, {"subtype": "error_max_turns"},
                       {"permission_denials": [{"tool_name": "Bash"}]},
                       {"deferred_tool_use": {"name": "Read"}}, {"num_turns": 2},
                       {"num_turns": True}, {"result": ""}, {"result": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                backend.answer("opus5.5", self.result({**self.envelope(), **change}), self.root)
        for raw in ("not JSON", "[]", "x" * 65537):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                backend.answer("opus5.5", subprocess.CompletedProcess([], 0, raw, ""), self.root)

    def test_check_is_model_free_but_preflight_probes_opus_once_and_records_exact_model(self):
        output = io.StringIO()
        with patch.object(backend, "ROOT", self.root), \
                patch.object(backend.shutil, "which", side_effect=lambda name: "/cli/claude" if name == "claude" else None), \
                patch.object(backend, "ensure_chatgpt") as codex_auth, \
                patch.object(backend, "execute", return_value=self.result()) as execute, redirect_stdout(output):
            self.assertEqual(backend.main(["--check"]), 0)
            execute.assert_not_called()
            self.assertEqual(backend.main(["--preflight"]), 0)
        codex_auth.assert_not_called()
        execute.assert_called_once()
        command = execute.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], MODEL)
        self.assertLessEqual(execute.call_args.kwargs["timeout"], 60)
        self.assertIn("advisor=disabled", output.getvalue())
        self.assertIn(MODEL + " low: authenticated decision probe passed", output.getvalue())
        logs = list((self.root / "logs").glob("model-preflight-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn('"modelUsage"', logs[0].read_text())
        self.assertIn("FINAL ANSWER:", logs[0].read_text())

    def test_unavailable_or_mismatched_model_fails_preflight_without_retry_or_profile_change(self):
        original = (self.root / "model-profile.json").read_bytes()
        mismatch = deepcopy(self.envelope())
        mismatch["modelUsage"] = {"claude-opus-4-6": {"outputTokens": 14}}
        for response in (self.result(code=1), self.result(mismatch)):
            with self.subTest(code=response.returncode), patch.object(backend, "ROOT", self.root), \
                    patch.object(backend.shutil, "which", return_value="/cli"), \
                    patch.object(backend, "execute", return_value=response) as execute, redirect_stdout(io.StringIO()):
                self.assertEqual(backend.main(["--preflight"]), 1)
                execute.assert_called_once()
                self.assertEqual((self.root / "model-profile.json").read_bytes(), original)

    def test_haiku_rollback_changes_only_local_profile_without_model_calls(self):
        with patch.object(backend, "ROOT", self.root), patch.object(backend, "execute") as execute, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--profile", "haiku"]), 0)
            self.assertEqual(backend.main(["--profile", "opus5.5"]), 0)
        execute.assert_not_called()
        self.assertFalse(backend.advisor_enabled(self.root))
        self.assertEqual(backend.load_profile(self.root)["trading"], "opus5.5")

    def test_normal_harness_unwraps_opus_json_and_preserves_deadline(self):
        (self.root / "prompts").mkdir()
        (self.root / "prompts/buyer.txt").write_text("Return a compact JSON decision")
        text = '{"action":"accept","price":9,"reason":"within policy"}'
        with patch.object(harness, "ROOT", self.root), \
                patch.dict(os.environ, {"WEEK06_LOG_DIR": str(self.root / "logs")}), \
                patch.object(backend, "execute", return_value=self.result(self.envelope(text))) as execute:
            choice = harness.decide("buyer.txt", {}, lambda raw: harness.parse_buyer(raw, 10), timeout=15, max_attempts=1)
        self.assertEqual(choice["price"], 9)
        execute.assert_called_once()
        self.assertLessEqual(execute.call_args.kwargs["timeout"], 15)


if __name__ == "__main__":
    unittest.main()
