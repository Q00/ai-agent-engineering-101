"""Exercise the host's real MCP boundary with scripted model replies, no API key."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp import Client as RealClient
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from openai.types.chat import ChatCompletionMessage

import host


def message(name=None, arguments=None, content=None):
    value = {"role": "assistant", "content": content}
    if name:
        value["tool_calls"] = [{"id": f"call_{name}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments or {})}}]
    return ChatCompletionMessage.model_validate(value)


class ScriptedBackend:
    def __init__(self, replies):
        self.calls = 0
        self.replies = iter(replies)
        self.requests = []

    async def complete(self, messages, tools, log):
        self.calls += 1
        self.requests.append(copy.deepcopy(messages))
        return next(self.replies)


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_tools_retries_refusal_and_stops_after_move(self):
        server = MCPServer("test-market")
        actions = []

        @server.tool(annotations=ToolAnnotations(read_only_hint=True))
        def inspect_state() -> str:
            return "open; buyer turn"

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def custom_move(price: int) -> str:
            if price > 70:
                raise ToolError("buyer price limit is 70; same turn")
            actions.append(price)
            return "move accepted"

        backend = ScriptedBackend([message("inspect_state"),
                                   message("custom_move", {"price": 90}),
                                   message("custom_move", {"price": 70}),
                                   message("custom_move", {"price": 60})])
        logs = []
        with patch.object(host, "Client", side_effect=lambda _: RealClient(server)):
            result = await host.run_turn("http://unused/mcp", "transport-only-secret",
                                         "n-test", "buyer", "keyboard", 70, backend, logs.append)
        self.assertTrue(result["moved"])
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["tool_calls"], 3)
        self.assertEqual(actions, [70])
        self.assertIn("tool error:", backend.requests[2][-1]["content"])
        self.assertIn("70; same turn", backend.requests[2][-1]["content"])
        self.assertNotIn("transport-only-secret", json.dumps(logs))
        self.assertNotIn("transport-only-secret", json.dumps(backend.requests))
        self.assertEqual([e["name"] for e in logs if e["event"] == "tool_call"],
                         ["inspect_state", "custom_move", "custom_move"])

    async def test_final_answer_without_move_returns_control_to_runner(self):
        server = MCPServer("test-market")

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def move() -> str:
            return "ok"

        backend = ScriptedBackend([message(content="I cannot decide.")])
        with patch.object(host, "Client", side_effect=lambda _: RealClient(server)):
            result = await host.run_turn("http://unused/mcp", "secret", "n-test", "seller",
                                         "lamp", 35, backend, lambda _: None)
        self.assertFalse(result["moved"])
        self.assertEqual(result["reason"], "model_finished_without_move")
        self.assertEqual(result["tool_calls"], 0)

    async def test_bad_json_is_local_error_not_a_server_tool_call(self):
        server = MCPServer("test-market")

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def move() -> str:
            return "ok"

        malformed = message("move")
        malformed.tool_calls[0].function.arguments = "{"
        backend = ScriptedBackend([malformed, message("move")])
        logs = []
        with patch.object(host, "Client", side_effect=lambda _: RealClient(server)):
            result = await host.run_turn("http://unused/mcp", "secret", "n-test", "buyer",
                                         "lamp", 45, backend, logs.append)
        self.assertEqual(result["tool_calls"], 1)
        self.assertTrue(any(e["event"] == "host_argument_error" for e in logs))
        self.assertEqual(sum(e["event"] == "tool_call" for e in logs), 1)

    async def test_round_budget_returns_without_inventing_move(self):
        server = MCPServer("test-market")

        @server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def move() -> str:
            raise ToolError("refused")

        backend = ScriptedBackend([message("move"), message("move")])
        with patch.object(host, "Client", side_effect=lambda _: RealClient(server)):
            result = await host.run_turn("http://unused/mcp", "secret", "n-test", "buyer",
                                         "lamp", 45, backend, lambda _: None, max_model_rounds=2)
        self.assertFalse(result["moved"])
        self.assertEqual(result["reason"], "max_model_rounds")
        self.assertEqual(result["tool_calls"], 2)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_429_and_missing_choices_with_increasing_wait(self):
        class RateFailure(Exception):
            status_code = 429

        replies = iter([RateFailure("secret provider body"), SimpleNamespace(choices=[]),
                        SimpleNamespace(choices=[SimpleNamespace(message=message(content="done"),
                                                                  finish_reason="stop")],
                                        model="observed-model", usage=None)])
        async def create(**kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        waits = []
        async def sleep(delay):
            waits.append(delay)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        backend = host.OpenAIBackend(client=client, sleeper=sleep)
        logs = []
        result = await backend.complete([], [], logs.append)
        self.assertEqual(result.content, "done")
        self.assertEqual(backend.calls, 3)
        self.assertEqual(waits, [1.0, 2.0])
        self.assertNotIn("secret provider body", json.dumps(logs))
        self.assertEqual(logs[-1]["response_model"], "observed-model")
        self.assertIsNone(logs[-1]["usage"]["total_tokens"])

    async def test_permanent_error_has_no_provider_body(self):
        async def create(**kwargs):
            raise ValueError("private bearer credential")
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        backend = host.OpenAIBackend(client=client)
        logs = []
        with self.assertRaises(host.ModelCallError) as caught:
            await backend.complete([], [], logs.append)
        self.assertNotIn("private bearer credential", str(caught.exception))
        self.assertEqual(backend.calls, 1)


if __name__ == "__main__":
    unittest.main()
