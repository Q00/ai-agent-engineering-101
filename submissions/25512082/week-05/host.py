"""Week 01-style tool loop adapted to a remote MCP market."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from model_client import ModelClient, ToolCall


STATE_TOOL = "get_negotiation"
MOVE_TOOLS = {
    "propose",
    "accept_proposal",
    "reject_proposal",
    "refuse",
}
MAX_MODEL_CALLS_PER_TURN = 2
MAX_TOOL_CALLS_PER_TURN = 2


class HostError(RuntimeError):
    """The model or MCP endpoint did not complete one valid agent turn."""


class ToolGateway(Protocol):
    async def list_tools(self) -> list[dict]: ...

    async def call_tool(self, name: str, arguments: dict) -> str: ...


@dataclass(frozen=True)
class HostTurn:
    action: str
    result: str
    model_calls: int
    tool_calls: int


def _assistant_message(content: str | None, call: ToolCall) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
        ],
    }


async def run_turn(
    model: ModelClient,
    gateway: ToolGateway,
    system_prompt: str,
    negotiation_id: str,
) -> HostTurn:
    """Read state and make at most one state-changing move."""
    tools = await gateway.list_tools()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"Take one negotiation turn for {negotiation_id}. First inspect it "
                "with get_negotiation, then call exactly one move tool."
            ),
        },
    ]
    tool_calls = 0

    for model_call in range(1, MAX_MODEL_CALLS_PER_TURN + 1):
        decision = model.complete_with_tools(messages, tools)
        if len(decision.calls) != 1:
            raise HostError("the model must return exactly one tool call at a time")
        call = decision.calls[0]
        if call.name not in {STATE_TOOL, *MOVE_TOOLS}:
            raise HostError(f"unsupported market tool: {call.name}")
        if call.arguments.get("negotiation_id") != negotiation_id:
            raise HostError("the model used a different negotiation_id")
        if tool_calls >= MAX_TOOL_CALLS_PER_TURN:
            raise HostError("tool-call limit reached")

        result = await gateway.call_tool(call.name, call.arguments)
        tool_calls += 1
        messages.append(_assistant_message(decision.content, call))
        messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": result}
        )

        if call.name in MOVE_TOOLS:
            return HostTurn(call.name, result, model_call, tool_calls)

    raise HostError("the model did not make a move within two model calls")


class SessionGateway:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def list_tools(self) -> list[dict]:
        result = await self.session.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                },
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.session.call_tool(name, arguments)
        return result.model_dump_json()


@asynccontextmanager
async def connect_market(url: str, bearer_token: str):
    """Yield one initialized MCP session carrying a party bearer token."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=60.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield SessionGateway(session)
