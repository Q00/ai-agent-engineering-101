"""Unit tests for the bounded Week 01-style MCP host loop."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from host import HostError, run_turn
from model_client import (
    CallMeter,
    ModelClient,
    ModelConfig,
    ProviderResponseError,
    ToolCall,
    ToolDecision,
)


TOOLS = [
    {"type": "function", "function": {"name": "get_negotiation"}},
    {"type": "function", "function": {"name": "propose"}},
]


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return TOOLS

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return f'{name}:ok'


class FakeModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = []

    def complete_with_tools(self, messages, tools):
        self.calls.append((list(messages), tools))
        return next(self.decisions)


def decision(name, arguments, call_id="call-1"):
    return ToolDecision(None, (ToolCall(call_id, name, arguments),))


class HostLoopTests(unittest.TestCase):
    def test_reads_then_makes_one_move_in_two_model_calls(self):
        model = FakeModel(
            [
                decision("get_negotiation", {"negotiation_id": "neg-1"}),
                decision(
                    "propose",
                    {"negotiation_id": "neg-1", "price": 45},
                    "call-2",
                ),
            ]
        )
        gateway = FakeGateway()

        result = asyncio.run(run_turn(model, gateway, "buyer prompt", "neg-1"))

        self.assertEqual((result.model_calls, result.tool_calls), (2, 2))
        self.assertEqual(result.action, "propose")
        self.assertEqual(
            gateway.calls,
            [
                ("get_negotiation", {"negotiation_id": "neg-1"}),
                ("propose", {"negotiation_id": "neg-1", "price": 45}),
            ],
        )
        second_messages = model.calls[1][0]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-1")

    def test_direct_move_finishes_after_one_model_and_tool_call(self):
        model = FakeModel(
            [decision("refuse", {"negotiation_id": "neg-1"})]
        )
        gateway = FakeGateway()

        result = asyncio.run(run_turn(model, gateway, "buyer prompt", "neg-1"))

        self.assertEqual((result.model_calls, result.tool_calls), (1, 1))
        self.assertEqual(result.action, "refuse")

    def test_second_read_stops_at_two_model_calls(self):
        model = FakeModel(
            [
                decision("get_negotiation", {"negotiation_id": "neg-1"}),
                decision("get_negotiation", {"negotiation_id": "neg-1"}, "call-2"),
            ]
        )
        gateway = FakeGateway()

        with self.assertRaisesRegex(HostError, "within two model calls"):
            asyncio.run(run_turn(model, gateway, "buyer prompt", "neg-1"))

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(gateway.calls), 2)

    def test_parallel_calls_and_wrong_negotiation_are_rejected_before_mcp(self):
        cases = [
            ToolDecision(
                None,
                (
                    ToolCall("a", "get_negotiation", {"negotiation_id": "neg-1"}),
                    ToolCall("b", "propose", {"negotiation_id": "neg-1", "price": 45}),
                ),
            ),
            decision("propose", {"negotiation_id": "neg-2", "price": 45}),
        ]
        for bad_decision in cases:
            with self.subTest(bad_decision=bad_decision):
                gateway = FakeGateway()
                with self.assertRaises(HostError):
                    asyncio.run(
                        run_turn(FakeModel([bad_decision]), gateway, "prompt", "neg-1")
                    )
                self.assertEqual(gateway.calls, [])


def config(max_retries=2):
    return ModelConfig(
        provider="test",
        model="test-model",
        base_url="https://example.invalid/v1",
        temperature=0,
        max_tokens=32,
        max_retries=max_retries,
        backoff_seconds=1,
    )


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers={})


class ModelToolTests(unittest.TestCase):
    def client(self, responses, max_retries=2):
        sleeps = []
        meter = CallMeter()
        client = ModelClient(config(max_retries), meter=meter, sleep=sleeps.append)
        create = Mock(side_effect=responses)
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        return client, create, meter, sleeps

    @staticmethod
    def response(name="propose", arguments='{"negotiation_id":"neg-1","price":45}'):
        call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name=name, arguments=arguments),
        )
        message = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_tool_response_is_normalized_and_tools_are_sent(self):
        client, create, meter, _sleeps = self.client([self.response()])

        result = client.complete_with_tools([{"role": "user", "content": "go"}], TOOLS)

        self.assertEqual(result.calls[0].name, "propose")
        self.assertEqual(result.calls[0].arguments["price"], 45)
        self.assertEqual(meter.agent_calls, 1)
        self.assertEqual(create.call_args.kwargs["tools"], TOOLS)

    def test_5xx_and_empty_choices_retry_with_a_bound(self):
        client, create, meter, sleeps = self.client(
            [HttpError(503), SimpleNamespace(choices=[]), self.response()]
        )

        result = client.complete_with_tools([], TOOLS)

        self.assertEqual(result.calls[0].name, "propose")
        self.assertEqual(create.call_count, 3)
        self.assertEqual(meter.agent_calls, 3)
        self.assertEqual(sleeps, [1, 2])

    def test_invalid_tool_arguments_fail_without_an_api_retry(self):
        client, create, _meter, sleeps = self.client([self.response(arguments="not-json")])

        with self.assertRaisesRegex(ProviderResponseError, "not valid JSON"):
            client.complete_with_tools([], TOOLS)

        self.assertEqual(create.call_count, 1)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
