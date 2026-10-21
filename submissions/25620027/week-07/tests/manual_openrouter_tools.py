from __future__ import annotations

import json

import anyio

from http_client import create_client
from market_a2a.harness import Harness
from market_a2a.model_provider import create_model_client
from market_a2a.models import ToolResult, ToolSpec


async def run() -> None:
    async def dispatch(name: str, arguments: str) -> ToolResult:
        match name:
            case "lookup":
                return ToolResult(data={"price": 100})
            case "accept":
                assert json.loads(arguments)["price"] == 100
                return ToolResult(data={"price": 100}, terminal=True)
            case _:
                message = "Unexpected smoke-test tool"
                raise AssertionError(message)

    tools = (
        ToolSpec(name="lookup", description="Read the fictional price",
                 parameters={"type": "object", "properties": {}}),
        ToolSpec(name="accept", description="Accept the fictional price",
                 parameters={"type": "object",
                             "properties": {"price": {"type": "integer"}},
                             "required": ["price"]}),
    )
    async with (
        create_client(model=True) as http,
        create_model_client(http) as client,
    ):
        harness = Harness(client, print)
        result = await harness.run(
            "Call lookup, then accept its exact observed price. Use tools only.",
            {"item": "fictional lamp"}, tools, dispatch,
        )
        assert result.terminal
        assert harness.calls == 2
        print(json.dumps({"model": harness.model,
                          "successful_calls": harness.calls,
                          "input_tokens": harness.input_tokens,
                          "output_tokens": harness.output_tokens,
                          "result": result.data,
                          "market_transaction": False}))


if __name__ == "__main__":
    anyio.run(run)
