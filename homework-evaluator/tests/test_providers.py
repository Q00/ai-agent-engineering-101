"""Transport contract tests: no paid calls and no installed model required."""

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_review import review_fixture

from ax_evaluator.providers import (
    CODEX_ISOLATION,
    ProviderFailure,
    ProviderUnavailable,
    cli_review,
    http_review,
    judge,
    validate_config,
)
from ax_evaluator.storage import canonical, read_json


class ProviderTest(unittest.TestCase):
    def test_openai_compatible_request_and_local_no_key(self):
        _, bundle, review = review_fixture()
        opener = Mock()
        opener.open.return_value = io.BytesIO(
            json.dumps(
                {
                    "choices": [{"message": {"content": canonical(review)}}],
                    "model": "fixture",
                }
            ).encode()
        )
        config = {
            "kind": "openai",
            "model": "fixture",
            "base_url": "http://localhost:11434/v1",
            "api_key_env": "",
            "structured_output": True,
        }
        with patch("urllib.request.build_opener", return_value=opener):
            text, _ = http_review(config, "evidence", bundle["response_schema"])
        self.assertEqual(json.loads(text), review)
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertNotIn("Authorization", request.headers)

    def test_anthropic_native_request(self):
        _, bundle, review = review_fixture()
        opener = Mock()
        opener.open.return_value = io.BytesIO(
            json.dumps(
                {"content": [{"type": "text", "text": canonical(review)}]}
            ).encode()
        )
        with (
            patch.dict("os.environ", {"TEST_AX_KEY": "fake-key-for-test"}),
            patch("urllib.request.build_opener", return_value=opener),
        ):
            text, _ = http_review(
                {"kind": "anthropic", "model": "fixture", "api_key_env": "TEST_AX_KEY"},
                "evidence",
                bundle["response_schema"],
            )
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1/messages"))
        self.assertEqual(json.loads(request.data)["max_tokens"], 8192)
        self.assertEqual(json.loads(text), review)

    def test_cli_adapters_use_no_student_working_directory_or_tools(self):
        _, bundle, review = review_fixture()
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args, 0, stdout="fixture-cli", stderr=""
                )
            if args[0] == "codex":
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    canonical(review)
                )
                return subprocess.CompletedProcess(
                    args, 0, stdout='{"type":"turn.completed"}', stderr=""
                )
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps({"structured_output": review}), stderr=""
            )

        with patch("subprocess.run", side_effect=run):
            for kind in ("codex", "claude"):
                text, meta = cli_review(
                    {"kind": kind, "model": "fixture"},
                    "evidence",
                    bundle["response_schema"],
                )
                self.assertEqual(json.loads(text), review)
                self.assertEqual(meta["cli_version"], "fixture-cli")
        codex_args = calls[1][0]
        self.assertIn("--ignore-user-config", codex_args)
        self.assertIn("project_doc_max_bytes=0", codex_args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_args)
        for feature in CODEX_ISOLATION:
            self.assertIn(feature, codex_args)
        claude_args = calls[3][0]
        self.assertEqual(claude_args[claude_args.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", claude_args)
        settings = json.loads(claude_args[claude_args.index("--settings") + 1])
        self.assertEqual(settings["claudeMdExcludes"], ["**"])
        self.assertFalse(settings["autoMemoryEnabled"])

    def test_codex_jsonl_failure_is_classified_without_echoing_error_body(self):
        secret = "private-text-that-must-not-appear-in-errors"

        def run(args, **kwargs):
            if "--version" in args:
                return subprocess.CompletedProcess(args, 0, "fixture", "")
            return subprocess.CompletedProcess(
                args,
                1,
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {
                            "message": "Usage limit exceeded " + secret,
                            "codexErrorInfo": "UsageLimitExceeded",
                        },
                    }
                ),
                "",
            )

        with patch("subprocess.run", side_effect=run):
            with self.assertRaises(ProviderUnavailable) as caught:
                cli_review({"kind": "codex", "model": "fixture"}, "evidence", {})
        self.assertEqual(caught.exception.category, "usage-limit")
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, json.dumps(caught.exception.metadata))

    def test_failed_turn_cannot_pass_even_with_a_final_file_and_zero_exit(self):
        def run(args, **kwargs):
            if "--version" in args:
                return subprocess.CompletedProcess(args, 0, "fixture", "")
            Path(args[args.index("--output-last-message") + 1]).write_text(
                '{"ok":true}'
            )
            return subprocess.CompletedProcess(
                args,
                0,
                '{"type":"turn.failed","error":{"message":"connection failed"}}',
                "",
            )

        with patch("subprocess.run", side_effect=run):
            with self.assertRaises(ProviderFailure) as caught:
                cli_review({"kind": "codex", "model": "fixture"}, "evidence", {})
        self.assertEqual(caught.exception.category, "connection")

    def test_action_events_cannot_be_accepted_as_a_text_only_review(self):
        for action in ("command_execution", "mcp_tool_call", "future_action"):

            def run(args, **kwargs):
                if "--version" in args:
                    return subprocess.CompletedProcess(args, 0, "fixture", "")
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    '{"ok":true}'
                )
                events = [
                    {"type": "item.completed", "item": {"type": action}},
                    {"type": "turn.completed"},
                ]
                return subprocess.CompletedProcess(
                    args, 0, "\n".join(map(json.dumps, events)), ""
                )

            with patch("subprocess.run", side_effect=run):
                with self.assertRaises(ProviderFailure) as caught:
                    cli_review({"kind": "codex", "model": "fixture"}, "evidence", {})
            self.assertEqual(caught.exception.category, "prohibited-tool-action")

    def test_known_disabled_feature_notice_is_not_an_executed_action(self):
        def run(args, **kwargs):
            if "--version" in args:
                return subprocess.CompletedProcess(args, 0, "fixture", "")
            Path(args[args.index("--output-last-message") + 1]).write_text(
                '{"ok":true}'
            )
            events = [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "error",
                        "message": "Code Mode is unavailable because "
                        "code-mode host is disabled.",
                    },
                },
                {"type": "turn.completed"},
            ]
            return subprocess.CompletedProcess(
                args, 0, "\n".join(map(json.dumps, events)), ""
            )

        with patch("subprocess.run", side_effect=run):
            text, metadata = cli_review(
                {"kind": "codex", "model": "fixture"}, "evidence", {}
            )
        self.assertEqual(json.loads(text), {"ok": True})
        self.assertEqual(metadata["nonfatal_notice_count"], 1)

    def test_malformed_response_saved_but_not_scored(self):
        snapshot, bundle, _ = review_fixture()
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "call"
            with patch(
                "ax_evaluator.providers.http_review", return_value=("not JSON", {})
            ):
                with self.assertRaises(ValueError):
                    judge(bundle, snapshot, {"kind": "openai", "model": "fixture"}, out)
            self.assertEqual((out / "raw-response.txt").read_text(), "not JSON")
            self.assertFalse((out / "response.json").exists())
            self.assertEqual(
                read_json(out / "failure.json")["status"], "no-score-produced"
            )

    def test_codex_usage_includes_cache_and_reasoning_without_tool_actions(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if "--version" in args:
                return subprocess.CompletedProcess(args, 0, "fixture", "")
            Path(args[args.index("--output-last-message") + 1]).write_text(
                '{"ok":true}'
            )
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 50,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                        },
                    }
                ),
                "",
            )

        with patch("subprocess.run", side_effect=run):
            _, meta = cli_review(
                {"kind": "codex", "model": "fixture", "effort": "low"}, "evidence", {}
            )
        self.assertIn("--json", calls[-1])
        self.assertIn('model_reasoning_effort="low"', calls[-1])
        self.assertEqual(meta["usage"]["cached_input_tokens"], 50)
        self.assertEqual(meta["usage"]["reasoning_output_tokens"], 5)

    def test_transport_failure_never_retries(self):
        snapshot, bundle, _ = review_fixture()
        with tempfile.TemporaryDirectory() as temp:
            with patch(
                "ax_evaluator.providers.http_review",
                side_effect=TimeoutError("timeout"),
            ) as transport:
                with self.assertRaises(TimeoutError):
                    judge(
                        bundle,
                        snapshot,
                        {"kind": "openai", "model": "fixture"},
                        Path(temp) / "call",
                    )
                self.assertEqual(transport.call_count, 1)

    def test_protocol_override_and_embedded_keys_rejected(self):
        for extra in ({"api_key": "secret"}, {"parameters": {"messages": []}}):
            with self.assertRaises(ValueError):
                validate_config({"kind": "openai", "model": "fixture", **extra})


if __name__ == "__main__":
    unittest.main()
