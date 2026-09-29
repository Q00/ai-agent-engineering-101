"""One-turn OpenAI host that discovers and calls the bearer-gated MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, assert_never

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent, Tool
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from pydantic import JsonValue, TypeAdapter

from market.http_client import create_async_client
from market.models import FrozenModel, NegotiationId, Role, Scenario

if TYPE_CHECKING:
    from market.settings import ExperimentConfig

MOVE_TOOLS: Final = frozenset(
    {"propose", "accept_proposal", "reject_proposal", "refuse"}
)
MAX_MODEL_ROUNDS: Final = 4
HTTP_RATE_LIMITED: Final = 429
HTTP_SERVER_ERROR: Final = 500


class HostRequest(FrozenModel):
    """Inputs for one party's isolated host run."""

    server_url: str
    token: str
    negotiation_id: NegotiationId
    role: Role
    scenario: Scenario


class HostTurn(FrozenModel):
    """Observable completion state of one host run."""

    committed: bool
    model_calls: int
    lines: tuple[str, ...]


class ModelCallError(RuntimeError):
    """The bounded model retry schedule was exhausted."""


class FunctionCall(FrozenModel):
    """Normalized function call excluding unsupported custom tools."""

    call_id: str
    name: str
    arguments: str


def system_prompt(role: Role, scenario: Scenario) -> str:
    """Return the same role prompt for every experimental condition."""
    match role:
        case Role.BUYER:
            boundary = (
                f"Your authorized maximum budget is {scenario.budget}. "
                "Never propose or accept a price above it."
            )
            objective = "Seek the lowest acceptable deal."
        case Role.SELLER:
            boundary = (
                f"Your authorized minimum reserve is {scenario.reserve}. "
                "Never propose or accept a price below it."
            )
            objective = "Seek the highest acceptable deal."
        case unreachable:
            assert_never(unreachable)
    return (
        f"You are the {role.value} negotiating {scenario.item}. {boundary} {objective} "
        "Use the market tools only. First call get_negotiation, then make one move. "
        "If a move is refused, read the tool error and make a corrected move in this same turn."
    )


def _openai_tool(tool: Tool) -> ChatCompletionToolParam:
    return ChatCompletionToolParam(
        type="function",
        function={
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    )


def _function_calls(message: ChatCompletionMessage) -> tuple[FunctionCall, ...]:
    normalized: list[FunctionCall] = []
    for call in message.tool_calls or []:
        match call.type:
            case "function":
                normalized.append(
                    FunctionCall(
                        call_id=call.id,
                        name=call.function.name,
                        arguments=call.function.arguments,
                    )
                )
            case "custom":
                continue
            case unreachable:
                assert_never(unreachable)
    return tuple(normalized)


def _assistant_message(message: ChatCompletionMessage) -> ChatCompletionAssistantMessageParam:
    tool_calls = [
        ChatCompletionMessageFunctionToolCallParam(
            id=call.call_id,
            type="function",
            function={
                "name": call.name,
                "arguments": call.arguments,
            },
        )
        for call in _function_calls(message)
    ]
    return ChatCompletionAssistantMessageParam(
        role="assistant",
        content=message.content,
        tool_calls=tool_calls,
    )


def _tool_text(content: list[TextContent]) -> str:
    return "\n".join(item.text for item in content)


async def _complete(
    model: AsyncOpenAI,
    config: ExperimentConfig,
    messages: list[ChatCompletionMessageParam],
    tools: list[ChatCompletionToolParam],
) -> ChatCompletionMessage:
    attempts = 0
    while True:
        try:
            response = await model.chat.completions.create(
                model=config.model,
                messages=messages,
                tools=tools,
                tool_choice="required",
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        except (APIConnectionError, APITimeoutError) as exc:
            retryable = True
            detail = exc.__class__.__name__
        except APIStatusError as exc:
            retryable = exc.status_code == HTTP_RATE_LIMITED or exc.status_code >= HTTP_SERVER_ERROR
            detail = f"HTTP {exc.status_code}"
        else:
            if response.choices:
                return response.choices[0].message
            retryable = True
            detail = "missing choices"
        if not retryable or attempts >= len(config.retry_delays_s):
            message = f"model call failed after {attempts + 1} attempt(s): {detail}"
            raise ModelCallError(message)
        await anyio.sleep(config.retry_delays_s[attempts])
        attempts += 1


async def run_host_turn(
    model: AsyncOpenAI,
    config: ExperimentConfig,
    request: HostRequest,
) -> HostTurn:
    """Connect with one party token and commit at most one valid market move."""
    headers = {"Authorization": f"Bearer {request.token}"}
    lines: list[str] = []
    model_calls = 0
    async with create_async_client(headers=headers, long_read=True) as http_client:
        transport = streamable_http_client(request.server_url, http_client=http_client)
        async with Client(transport) as market:
            listed = await market.list_tools()
            tools = [_openai_tool(tool) for tool in listed.tools]
            messages: list[ChatCompletionMessageParam] = [
                ChatCompletionSystemMessageParam(
                    role="system",
                    content=system_prompt(request.role, request.scenario),
                ),
                ChatCompletionUserMessageParam(
                    role="user",
                    content=f"Take your turn in negotiation {request.negotiation_id}.",
                ),
            ]
            for _round in range(MAX_MODEL_ROUNDS):
                reply = await _complete(model, config, messages, tools)
                model_calls += 1
                messages.append(_assistant_message(reply))
                calls = _function_calls(reply)
                for call in calls:
                    arguments = TypeAdapter(dict[str, JsonValue]).validate_json(call.arguments)
                    result = await market.call_tool(call.name, arguments)
                    text_parts = [item for item in result.content if isinstance(item, TextContent)]
                    rendered = _tool_text(text_parts)
                    status = "ERROR" if result.is_error else "OK"
                    lines.append(
                        f"[tool] {call.name}({arguments}) -> {status}: {rendered}"
                    )
                    messages.append(
                        ChatCompletionToolMessageParam(
                            role="tool",
                            tool_call_id=call.call_id,
                            content=rendered,
                        )
                    )
                    if call.name in MOVE_TOOLS and not result.is_error:
                        return HostTurn(
                            committed=True,
                            model_calls=model_calls,
                            lines=tuple(lines),
                        )
    return HostTurn(committed=False, model_calls=model_calls, lines=tuple(lines))
