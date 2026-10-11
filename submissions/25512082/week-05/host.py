"""Week 01-style tool loop adapted to a remote MCP market."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable, Protocol

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
MAX_RECOVERY_CALLS_PER_TURN = 2


class HostError(RuntimeError):
    """The model or MCP endpoint did not complete one valid agent turn."""


class ToolGateway(Protocol):
    async def list_tools(self) -> list[dict]: ...

    async def call_tool(self, name: str, arguments: dict) -> "GatewayResult": ...


@dataclass(frozen=True)
class GatewayResult:
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class HostTurn:
    action: str
    result: str
    model_calls: int
    tool_calls: int
    refused_calls: int


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
    log: Callable[[str], None] | None = None,
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
    emit = log or (lambda _message: None)

    maximum_calls = MAX_MODEL_CALLS_PER_TURN + MAX_RECOVERY_CALLS_PER_TURN
    refused_calls = 0
    for model_call in range(1, maximum_calls + 1):
        decision = model.complete_with_tools(messages, tools)
        if len(decision.calls) != 1:
            raise HostError("the model must return exactly one tool call at a time")
        call = decision.calls[0]
        if call.name not in {STATE_TOOL, *MOVE_TOOLS}:
            raise HostError(f"unsupported market tool: {call.name}")
        if call.arguments.get("negotiation_id") != negotiation_id:
            raise HostError("the model used a different negotiation_id")
        if tool_calls >= MAX_TOOL_CALLS_PER_TURN + MAX_RECOVERY_CALLS_PER_TURN:
            raise HostError("tool-call limit reached")

        emit(f"[tool-call] name={call.name} arguments={json.dumps(call.arguments, sort_keys=True)}")
        result = await gateway.call_tool(call.name, call.arguments)
        tool_calls += 1
        emit(f"[tool-result] name={call.name} error={int(result.is_error)} result={result.content}")
        messages.append(_assistant_message(decision.content, call))
        messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": result.content}
        )

        if result.is_error:
            refused_calls += 1
            continue
        if call.name in MOVE_TOOLS:
            return HostTurn(
                call.name,
                result.content,
                model_call,
                tool_calls,
                refused_calls,
            )

        if model_call >= MAX_MODEL_CALLS_PER_TURN and refused_calls == 0:
            break

    raise HostError("the model did not make a valid move within the call limit")


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

    async def call_tool(self, name: str, arguments: dict) -> GatewayResult:
        result = await self.session.call_tool(name, arguments)
        return GatewayResult(result.model_dump_json(), result.is_error)


@asynccontextmanager
async def connect_market(url: str, bearer_token: str):
    """Yield one initialized MCP session carrying a party bearer token."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=60.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield SessionGateway(session)
