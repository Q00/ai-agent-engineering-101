"""Profile isolation, CLI contracts and preflight; no auth/model/network calls."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import harness
import model_backend as backend

ROOT = Path(__file__).resolve().parent


class ModelBackendTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = self.root / "model-profile.json"

    def write_profile(self, trading="astra"):
        self.profile.write_text(json.dumps({"version": 1, "trading": trading, "advisor": "haiku"}))

    def result(self, events=None, code=0):
        return subprocess.CompletedProcess([], code, "\n".join(json.dumps(x) for x in
            (events if events is not None else [{"type": "turn.completed"}])), "")

    def test_scope_and_reversible_local_profile_do_not_start_a_service(self):
        self.assertEqual(backend.required_executables(self.root), ("claude",))
        self.write_profile()
        for prompt in ("seller_inventory.txt", "buyer.txt", "market_select.txt", "seller_opening.txt"):
            self.assertEqual(backend.for_prompt(prompt, self.root), "astra")
        self.assertEqual(backend.for_prompt("sales_advisor.txt", self.root), "haiku")
        with patch.object(backend, "ROOT", self.root), patch.object(backend, "execute") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--profile", "haiku"]), 0)
            run.assert_not_called()
        self.assertEqual(backend.required_executables(self.root), ("claude",))

    def test_bad_profile_never_silently_falls_back(self):
        for cfg in ({"version": 1, "trading": "typo", "advisor": "haiku"},
                    {"version": 1, "trading": "astra", "advisor": "astra"},
                    {"version": True, "trading": "astra", "advisor": "haiku"}, []):
            self.profile.write_text(json.dumps(cfg))
            with self.assertRaises(ValueError):
                backend.load_profile(self.root)

    def test_astra_command_is_readonly_ephemeral_low_and_uses_stdin(self):
        cmd = backend.command("astra", self.root, "buyer.txt")
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        self.assertEqual(cmd[-1], "-")
        for value in ("--ephemeral", "--ignore-user-config", "--ignore-rules",
                      'model_reasoning_effort="low"', 'forced_login_method="chatgpt"',
                      'web_search="disabled"', 'project_doc_max_bytes=0'):
            self.assertIn(value, cmd)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        schema = json.loads((self.root / "schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"action", "price", "reason"})

    def test_schema_preserves_selection_and_opening_contracts(self):
        self.assertEqual(set(backend.schema_for("market_select.txt")["required"]), {"name", "sku", "reason"})
        self.assertEqual(set(backend.schema_for("seller_opening_value.txt")["required"]), {"price", "reason"})
        self.assertEqual(backend.command("haiku", self.root, "sales_advisor.txt"), backend.CLAUDE_COMMAND)

    def test_api_env_is_removed_without_repurposing_home_or_cli_auth(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "do-not-send", "CODEX_API_KEY": "do-not-send",
                                    "OPENAI_BASE_URL": "https://unwanted.example", "CODEX_HOME": "/existing/auth", "HOME": "/existing/home"}):
            env = backend.environment("astra")
        self.assertEqual(env["CODEX_HOME"], "/existing/auth")
        self.assertEqual(env["HOME"], "/existing/home")
        self.assertTrue(all(k not in env for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")))

    def test_auth_requires_chatgpt_and_never_prints_or_changes_credentials(self):
        for text, code in (("Logged in using ChatGPT", 0), ("Logged in using an API key SECRET", 0), ("Not logged in", 1)):
            result = subprocess.CompletedProcess([], code, "", text)
            with patch.object(backend, "execute", return_value=result) as run:
                if code == 0 and "ChatGPT" in text:
                    backend.ensure_chatgpt()
                else:
                    with self.assertRaisesRegex(ValueError, "ChatGPT login") as error:
                        backend.ensure_chatgpt()
                    self.assertNotIn("SECRET", str(error.exception))
                self.assertEqual(run.call_args.args[0], ["codex", "login", "status"])

    def test_final_file_is_used_and_tools_fail_even_with_valid_json(self):
        text = '{"action":"accept","price":10,"reason":"ok"}'
        (self.root / "answer.json").write_text(text)
        self.assertEqual(backend.answer("astra", self.result(), self.root), text)
        for kind in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            events = [{"type": "item.completed", "item": {"type": kind}}, {"type": "turn.completed"}]
            with self.assertRaisesRegex(ValueError, "tool"):
                backend.answer("astra", self.result(events), self.root)
        for item in (None, [], "invalid"):
            with self.assertRaises(ValueError):
                backend.answer("astra", self.result([{"type": "item.completed", "item": item}]), self.root)

    def test_missing_oversized_and_incomplete_final_outputs_fail(self):
        with self.assertRaises(ValueError):
            backend.answer("astra", self.result(), self.root)
        path = self.root / "answer.json"
        path.write_text("x" * 65537)
        with self.assertRaises(ValueError):
            backend.answer("astra", self.result(), self.root)
        path.write_text("{}")
        for events in ([], [{"type": "turn.failed"}], [{"type": "surprise"}]):
            with self.assertRaises(ValueError):
                backend.answer("astra", self.result(events), self.root)

    def test_call_uses_same_timeout_and_logs_raw_stream_and_final(self):
        log = io.StringIO()
        def run(cmd, **kwargs):
            self.assertEqual(kwargs["prompt"], "PRIVATE TASK")
            self.assertLessEqual(kwargs["timeout"], 30)
            (Path(kwargs["cwd"]) / "answer.json").write_text('{"ready":true,"reason":"ok"}')
            return self.result()
        with patch.object(backend, "ensure_chatgpt") as auth, patch.object(backend, "execute", side_effect=run):
            self.assertIn('"ready":true', backend.call("astra", "preflight", "PRIVATE TASK", timeout=30, log=log))
            auth.assert_called_once()
        self.assertIn("turn.completed", log.getvalue())
        self.assertIn("FINAL ANSWER:", log.getvalue())

    def test_timeout_kills_process_group_and_preserves_output(self):
        process = Mock(pid=12345, returncode=-9)
        process.communicate.side_effect = [subprocess.TimeoutExpired("codex", 1), ("partial", "timed out")]
        with patch.object(backend.subprocess, "Popen", return_value=process) as popen, \
                patch.object(backend.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired) as error:
                backend.execute(["codex"], timeout=1)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        kill.assert_called_once_with(12345, backend.signal.SIGKILL)
        self.assertEqual(error.exception.stdout, "partial")

    def test_interruption_reaps_the_cli_children_without_starting_a_retry(self):
        process = Mock(pid=12345, returncode=-9)
        process.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with patch.object(backend.subprocess, "Popen", return_value=process), patch.object(backend.os, "killpg") as kill:
            with self.assertRaises(KeyboardInterrupt):
                backend.execute(["codex"], timeout=30)
        kill.assert_called_once_with(12345, backend.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_harness_retries_invalid_decision_only_and_keeps_advisor_haiku(self):
        self.write_profile()
        (self.root / "prompts").mkdir()
        (self.root / "prompts/buyer.txt").write_text("Return a decision")
        (self.root / "prompts/sales_advisor.txt").write_text("Return advice")
        with patch.object(harness, "ROOT", self.root), patch.dict(os.environ, {"WEEK06_LOG_DIR": str(self.root / "logs")}), \
                patch.object(harness, "call_model", side_effect=["bad", '{"action":"accept","price":9,"reason":"ok"}']) as run:
            choice = harness.decide("buyer.txt", {}, lambda s: harness.parse_buyer(s, 10))
        self.assertEqual(choice["price"], 9)
        self.assertEqual([c.args[0] for c in run.call_args_list], ["astra", "astra"])
        with patch.object(harness, "ROOT", self.root), patch.dict(os.environ, {"WEEK06_LOG_DIR": str(self.root / "logs")}), \
                patch.object(harness, "call_model", return_value='{"reason":"ok"}') as run:
            harness.decide("sales_advisor.txt", {}, harness.parse_object, max_attempts=1)
        self.assertEqual(run.call_args.args[0], "haiku")

    def test_harness_auth_or_process_failure_never_retries_or_changes_model(self):
        self.write_profile()
        (self.root / "prompts").mkdir()
        (self.root / "prompts/buyer.txt").write_text("Return a decision")
        with patch.object(harness, "ROOT", self.root), patch.dict(os.environ, {"WEEK06_LOG_DIR": str(self.root / "logs")}), \
                patch.object(harness, "call_model", side_effect=ValueError("not authenticated")) as run:
            with self.assertRaises(harness.HarnessError):
                harness.decide("buyer.txt", {}, harness.parse_object)
        run.assert_called_once()

    def test_check_does_not_make_model_call_and_preflight_is_explicit(self):
        self.write_profile()
        with patch.object(backend, "ROOT", self.root), patch.object(backend.shutil, "which", return_value="/cli"), \
                patch.object(backend, "ensure_chatgpt") as auth, patch.object(backend, "call", return_value='{"ready":true,"reason":"ok"}') as model, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backend.main(["--check"]), 0)
            model.assert_not_called()
            auth.assert_called_once()
            self.assertEqual(backend.main(["--preflight"]), 0)
            model.assert_called_once()
            self.assertEqual(model.call_args.args[:2], ("astra", "preflight"))
        self.assertEqual(len(list((self.root / "logs").glob("model-preflight-*.log"))), 1)


if __name__ == "__main__":
    unittest.main()
