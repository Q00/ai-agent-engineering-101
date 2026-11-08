"""GPT-6.1 Sol routing and deployment gates; no live model, login, or network calls."""
from contextlib import redirect_stdout
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
MODEL = "gpt-6.1-sol"


class SolModelTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = self.root / "model-profile.json"
        self.profile.write_text(json.dumps({"version": 1, "trading": "sol", "advisor": "haiku"}))
        (self.root / "advisor-config.json").write_text('{"enabled":false}')
        (self.root / "prompts").mkdir()
        for name in ("seller_inventory.txt", "buyer.txt", "market_select.txt"):
            (self.root / "prompts" / name).write_text("Return a structured decision.")

    @staticmethod
    def result(events=None, code=0, error=""):
        events = [{"type": "turn.completed"}] if events is None else events
        return subprocess.CompletedProcess([], code, "\n".join(json.dumps(row) for row in events), error)

    def test_all_trading_roles_use_sol_without_requiring_inactive_claude(self):
        self.assertEqual(backend.required_executables(self.root), ("codex",))
        self.assertEqual(backend.active_profiles(self.root), ("sol",))
        for name in ("seller_inventory.txt", "buyer.txt", "market_select.txt", "seller_opening.txt"):
            self.assertEqual(backend.for_prompt(name, self.root), "sol")
        with self.assertRaisesRegex(ValueError, "disabled"):
            backend.for_prompt("sales_advisor.txt", self.root)

    def test_cli_profile_switch_is_local_and_preserves_disabled_advisor(self):
        self.profile.write_text('{"version":1,"trading":"opus5.5","advisor":"haiku"}')
        with patch.object(backend, "ROOT", self.root), patch.object(backend, "execute") as execute, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--profile", "sol"]), 0)
        execute.assert_not_called()
        self.assertEqual(backend.load_profile(self.root), {"version": 1, "trading": "sol", "advisor": "haiku"})
        self.assertFalse(backend.advisor_enabled(self.root))

    def test_each_trading_role_runs_exact_sol_low_with_schema_and_no_tools(self):
        fixtures = {
            "seller_inventory.txt": {"action": "counter", "price": 12, "reason": "above reserve"},
            "buyer.txt": {"action": "accept", "price": 10, "reason": "within policy"},
            "market_select.txt": {"name": "fixture-shop", "sku": None, "reason": "eligible stock"},
        }
        for prompt_name, decision in fixtures.items():
            def execute(command, **kwargs):
                self.assertEqual(command[:2], ["codex", "exec"])
                self.assertEqual(command[command.index("--model") + 1], MODEL)
                self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
                self.assertEqual(command[-1], "-")
                self.assertEqual(kwargs["prompt"], "fixture only")
                self.assertLessEqual(kwargs["timeout"], 30)
                for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules",
                             'model_reasoning_effort="low"', 'forced_login_method="chatgpt"',
                             'model_provider="openai"', 'web_search="disabled"',
                             'skills.include_instructions=false', 'project_doc_max_bytes=0'):
                    self.assertIn(flag, command)
                for feature in ("shell_tool", "apps", "plugins", "multi_agent", "browser_use", "memories"):
                    self.assertTrue(any(command[i:i + 2] == ["--disable", feature]
                                        for i in range(len(command) - 1)))
                self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
                schema_path = Path(command[command.index("--output-schema") + 1])
                schema = json.loads(schema_path.read_text())
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["required"]), set(decision))
                for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
                    self.assertNotIn(key, kwargs["env"])
                self.assertEqual(kwargs["env"]["CODEX_HOME"], "/fixture-existing-auth")
                Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(decision))
                return self.result()

            with self.subTest(prompt_name=prompt_name), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-unused", "CODEX_API_KEY": "fixture-unused",
                                            "OPENAI_BASE_URL": "https://fixture.invalid", "CODEX_HOME": "/fixture-existing-auth"}), \
                    patch.object(backend, "ensure_chatgpt") as auth, \
                    patch.object(backend, "execute", side_effect=execute) as run:
                output = backend.call("sol", prompt_name, "fixture only", timeout=30, log=io.StringIO())
            self.assertEqual(json.loads(output), decision)
            auth.assert_called_once()
            run.assert_called_once()

    def test_api_login_cannot_launch_sol_or_change_the_selected_profile(self):
        before = self.profile.read_bytes()
        result = subprocess.CompletedProcess([], 0, "", "Logged in using an API key FIXTURE-SECRET")
        with patch.object(backend, "execute", return_value=result) as run:
            with self.assertRaisesRegex(ValueError, "ChatGPT login") as error:
                backend.call("sol", "buyer.txt", "fixture", timeout=30, log=io.StringIO())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["codex", "login", "status"])
        self.assertNotIn("FIXTURE-SECRET", str(error.exception))
        self.assertEqual(self.profile.read_bytes(), before)

    def test_check_needs_codex_login_but_does_not_probe_or_require_claude(self):
        def which(name):
            return "/fixture/codex" if name == "codex" else None
        with patch.object(backend, "ROOT", self.root), \
                patch.object(backend.shutil, "which", side_effect=which), \
                patch.object(backend, "ensure_chatgpt") as auth, patch.object(backend, "call") as model, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--check"]), 0)
        auth.assert_called_once()
        model.assert_not_called()

    def test_preflight_probes_only_sol_once_with_inactive_haiku_advisor(self):
        output = io.StringIO()
        with patch.object(backend, "ROOT", self.root), \
                patch.object(backend.shutil, "which", return_value="/fixture/codex"), \
                patch.object(backend, "call", return_value='{"ready":true,"reason":"ok"}') as model, \
                redirect_stdout(output):
            self.assertEqual(backend.main(["--preflight"]), 0)
        model.assert_called_once()
        self.assertEqual(model.call_args.args[:2], ("sol", "preflight"))
        self.assertIn(MODEL + " low", output.getvalue())
        self.assertIn("advisor=disabled", output.getvalue())

    def test_unavailable_model_fails_preflight_without_retry_fallback_or_profile_change(self):
        before = self.profile.read_bytes()
        output = io.StringIO()
        with patch.object(backend, "ROOT", self.root), \
                patch.object(backend.shutil, "which", return_value="/fixture/codex"), \
                patch.object(backend, "call", side_effect=ValueError("Selected model is unavailable")) as model, \
                redirect_stdout(output):
            self.assertEqual(backend.main(["--preflight"]), 1)
        model.assert_called_once()
        self.assertEqual(model.call_args.args[0], "sol")
        self.assertEqual(self.profile.read_bytes(), before)
        self.assertIn("Selected model is unavailable", output.getvalue())

    def test_harness_uses_sol_and_does_not_retry_cli_failure(self):
        before = self.profile.read_bytes()
        for name in ("seller_inventory.txt", "buyer.txt", "market_select.txt"):
            with self.subTest(prompt_name=name), patch.object(harness, "ROOT", self.root), \
                    patch.dict(os.environ, {"WEEK06_LOG_DIR": str(self.root / "logs")}), \
                    patch.object(harness, "call_model", wraps=backend.call), \
                    patch.object(backend, "ensure_chatgpt"), \
                    patch.object(backend, "execute", return_value=self.result(code=1, error="usage exhausted")) as run:
                with self.assertRaises(harness.HarnessError):
                    harness.decide(name, {}, harness.parse_object)
            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--model") + 1], MODEL)
        self.assertEqual(self.profile.read_bytes(), before)

    def test_sol_tool_event_is_rejected_even_when_final_json_exists(self):
        (self.root / "answer.json").write_text('{"action":"accept","price":10,"reason":"ok"}')
        result = self.result([{"type": "item.completed", "item": {"type": "mcp_tool_call"}},
                              {"type": "turn.completed"}])
        with self.assertRaisesRegex(ValueError, "tool"):
            backend.answer("sol", result, self.root)


if __name__ == "__main__":
    unittest.main()
