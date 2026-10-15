"""Transport contract checks: no model requests and no invented trade decisions."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

import benchmark_transport as transport


class BenchmarkTransportTests(unittest.TestCase):
    def test_claude_flags_keep_original_system_and_stdin(self):
        messages, record = transport.parse_cli("claude", [
            "-p", "--model", "haiku", "--tools", "", "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", "시스템\nexact",
        ], "원문\n100?")
        self.assertEqual(messages, [{"role": "system", "content": "시스템\nexact"},
                                    {"role": "user", "content": "원문\n100?"}])
        self.assertEqual(record["stdin"], "원문\n100?")
        self.assertEqual(len(record["removed_flags"]), 7)

    def test_codex_flags_preserve_original_prompt(self):
        messages, record = transport.parse_cli("codex", ["exec", "--skip-git-repo-check",
            "--ephemeral", "--color", "never", "-"], "arbitrary original text\n")
        self.assertEqual(messages[-1]["content"], "arbitrary original text\n")
        self.assertEqual(record["argv"][:2], ["codex", "exec"])

    def test_unknown_source_flags_fail_closed(self):
        with self.assertRaises(transport.TransportError):
            transport.parse_cli("claude", ["-p", "--unknown-mode"], "source")

    def test_configuration_requires_absolute_native_binary(self):
        with patch.dict(transport.os.environ, {"A2A_BENCH_CODEX": "codex"}):
            with self.assertRaises(transport.TransportError):
                transport._configuration(trace_dir="/tmp/no-write-expected")

    def test_transport_envelope_preserves_message_text_and_order(self):
        messages = [{"role": "system", "content": 'x\n"quoted"'},
                    {"role": "user", "content": "한글"}]
        rendered = transport.render_request(messages)
        restored = json.loads(rendered.split("exact text):\n")[1])
        self.assertEqual(restored, messages)

    @contextmanager
    def isolated_slot(self, *args, **kwargs):
        self.assertNotIn("interval", kwargs)  # The shared policy resolves it centrally.
        yield "isolated-test-ticket"

    def assert_mock_inference(self, exit_code=0):
        # The binary is never run. Any existing absolute executable is sufficient
        # to validate configuration before the Popen boundary is mocked.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            process = Mock()
            process.pid, process.returncode = 99999999, exit_code
            process.poll.return_value = exit_code

            def popen(command, **kwargs):
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-sol")
                self.assertNotIn("--output-schema", command)
                output = Path(command[command.index("--output-last-message") + 1])
                self.assertEqual(kwargs["cwd"], str(output.parent))
                self.assertIn("--ignore-user-config", command)
                self.assertEqual(command[command.index("--sandbox") + 1], "read-only")

                def communicate(prompt, timeout=None):
                    self.assertEqual(timeout, 40)
                    self.assertIn("unchanged source prompt", prompt)
                    output.write_text("literal transport output\n", encoding="utf-8")
                    return "native trace", "native stderr"

                process.communicate.side_effect = communicate
                return process

            with patch.dict(transport.os.environ, {"A2A_INFERENCE_GAP_SECONDS": "5"}), \
                    patch.object(transport, "inference_slot", self.isolated_slot), \
                    patch.object(transport.subprocess, "Popen", popen):
                arguments = {"source": "test", "original_request": {"argv": []},
                             "codex": sys.executable, "trace_dir": directory}
                messages = [{"role": "user", "content": "unchanged source prompt"}]
                if exit_code:
                    with self.assertRaises(transport.TransportError):
                        transport.invoke(messages, **arguments)
                else:
                    final, _ = transport.invoke(messages, **arguments)
                    self.assertEqual(final, "literal transport output\n")
            artifact = next(directory.iterdir())
            request = json.loads((artifact / "request.json").read_text())
            self.assertEqual(request["queue_interval_seconds"], 5)
            self.assertEqual((artifact / "stdout.txt").read_text(), "native trace")
            self.assertEqual((artifact / "stderr.txt").read_text(), "native stderr")
            result = json.loads((artifact / "result.json").read_text())
            self.assertEqual(result["status"], "failed" if exit_code else "completed")
            with self.assertRaises(FileExistsError):
                transport._write(artifact / "result.json", {})

    def test_native_command_boundary_and_immutable_artifacts(self):
        self.assert_mock_inference()

    def test_native_failure_is_not_success_or_fallback(self):
        self.assert_mock_inference(7)

    def test_http_client_shape_and_failure_status(self):
        async def run():
            with tempfile.TemporaryDirectory() as temporary:
                app = transport.create_app(codex=sys.executable, trace_dir=temporary)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    health = await client.get("/health")
                    self.assertEqual(health.json()["model"], "gpt-6-sol")
                    with patch.object(transport, "invoke", return_value=("literal transport output", "id")) as invoke:
                        response = await client.post("/v1/chat/completions", json={
                            "model": "original-model", "temperature": 0.2,
                            "messages": [{"role": "user", "content": "source"}]})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["choices"][0]["message"]["content"],
                                         "literal transport output")
                        self.assertEqual(invoke.call_args.kwargs["original_request"]["body"]["model"],
                                         "original-model")
                    with patch.object(transport, "invoke", side_effect=transport.TransportError("failed")):
                        response = await client.post("/v1/chat/completions", json={
                            "messages": [{"role": "user", "content": "source"}]})
                        self.assertEqual(response.status_code, 502)
                    response = await client.post("/v1/chat/completions", json={"messages": []})
                    self.assertEqual(response.status_code, 400)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
