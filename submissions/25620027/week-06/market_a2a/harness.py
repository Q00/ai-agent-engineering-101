"""Week 05 function-tool host loop with local guarded A2A action dispatch.

The message conversion and model/tool/observation cycle derive from
week-05/market/host.py. Transport-specific MCP calls become typed local dispatch.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, assert_never, final

import anyio
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)
from openai.types.shared_params import FunctionDefinition

from market_a2a.models import HarnessError, ToolDispatch, ToolResult, ToolSpec

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

MODEL: Final = "gpt-4.1-nano-2025-04-14"
MAX_MODEL_ROUNDS: Final = 6
RETRY_STATUS: Final = 429
SERVER_ERROR: Final = 500
RETRY_DELAYS: Final = (1.0, 2.0, 4.0)


def assistant_message(
    message: ChatCompletionMessage,
) -> ChatCompletionAssistantMessageParam:
    """Preserve the Week 05 assistant tool-call identifiers for observations."""
    calls: list[ChatCompletionMessageFunctionToolCallParam] = []
    for call in message.tool_calls or []:
        match call.type:
            case "function":
                calls.append(
                    ChatCompletionMessageFunctionToolCallParam(
                        id=call.id,
                        type="function",
                        function={
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    ),
                )
                continue
            case "custom":
                continue
        assert_never(call)
    return ChatCompletionAssistantMessageParam(
        role="assistant",
        content=message.content,
        tool_calls=calls,
    )


@final
class Harness:
    """One shared client, bounded local contexts and guarded tool execution.

    Every run starts from a compact task snapshot. The adapter keeps authoritative
    negotiation state; full wire/tool traces remain outside the model context.
    """

    def __init__(self, client: AsyncOpenAI, emit: Callable[[str], None]) -> None:
        """Own observability counters while the caller owns the API client."""
        self.client = client
        self.emit = emit
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.limit = anyio.CapacityLimiter(5)

    async def run(
        self,
        prompt: str,
        state: JsonValue,
        tools: tuple[ToolSpec, ...],
        dispatch: ToolDispatch,
    ) -> ToolResult:
        """Let the model select tools until one authorized action is committed."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(state, ensure_ascii=False)},
        ]
        advertised: list[ChatCompletionToolParam] = [
            ChatCompletionFunctionToolParam(
                type="function",
                function=FunctionDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters=dict(tool.parameters.items()),
                ),
            )
            for tool in tools
        ]
        self.emit(f"CONTEXT chars={len(json.dumps(state))} tools={len(tools)}")
        for round_number in range(MAX_MODEL_ROUNDS):
            reply = await self.complete(messages, advertised)
            messages.append(assistant_message(reply))
            for call in reply.tool_calls or []:
                match call.type:
                    case "function":
                        self.emit(
                            "".join(
                                (
                                    f"MODEL_TOOL round={round_number + 1} ",
                                    f"{call.function.name} {call.function.arguments}",
                                ),
                            ),
                        )
                        result = await dispatch(
                            call.function.name, call.function.arguments
                        )
                        rendered = json.dumps(result.data, ensure_ascii=False)
                        self.emit(f"TOOL_RESULT {call.function.name} {rendered}")
                        messages.append(
                            ChatCompletionToolMessageParam(
                                role="tool",
                                tool_call_id=call.id,
                                content=rendered,
                            ),
                        )
                        if result.terminal:
                            return result
                        continue
                    case "custom":
                        message = "Unsupported custom tool response"
                        raise HarnessError(message)
                assert_never(call)
        message = "Six model rounds ended without an authorized action"
        raise HarnessError(message)

    async def complete(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        """Retry transient API errors without replacing the model's decision."""
        for attempt, delay in enumerate(RETRY_DELAYS):
            try:
                async with self.limit:
                    completion = await self.client.chat.completions.create(
                        model=MODEL,
                        temperature=0,
                        messages=messages,
                        tools=tools,
                        tool_choice="required",
                        parallel_tool_calls=False,
                        max_completion_tokens=450,
                    )
            except APIStatusError as exc:
                if exc.status_code != RETRY_STATUS and exc.status_code < SERVER_ERROR:
                    raise
                self.emit(f"MODEL_RETRY status={exc.status_code} attempt={attempt + 1}")
            except (APIConnectionError, APITimeoutError) as exc:
                self.emit(
                    f"MODEL_RETRY kind={type(exc).__name__} attempt={attempt + 1}"
                )
            else:
                self.calls += 1
                usage = completion.usage
                if usage is not None:
                    self.input_tokens += usage.prompt_tokens
                    self.output_tokens += usage.completion_tokens
                    self.emit(
                        "".join(
                            (
                                f"MODEL_USAGE model={MODEL} ",
                                f"input={usage.prompt_tokens} ",
                                f"output={usage.completion_tokens}",
                            ),
                        ),
                    )
                return completion.choices[0].message
            await anyio.sleep(delay)
        message = "Transient API retries exhausted"
        raise HarnessError(message)
