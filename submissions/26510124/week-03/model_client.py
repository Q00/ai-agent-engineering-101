"""Small OpenAI-compatible adapter shared by bidders and task agents."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class Backend(Protocol):
    model: str
    provider: str
    temperature: float

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply: ...


class OpenAIBackend:
    """One-call adapter for OpenAI and OpenAI-compatible providers.

    A client is created per call so concurrent bid requests do not share
    mutable SDK state. Conversation state is owned by ``Conversation``.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        request_timeout: float = 90.0,
    ):
        self.model = model
        self.temperature = temperature
        self.request_timeout = request_timeout
        self.provider = "openrouter" if "openrouter.ai" in os.environ.get(
            "OPENAI_BASE_URL", ""
        ) else "openai-compatible"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        from openai import OpenAI

        client = OpenAI(timeout=self.request_timeout)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
                for tool in tools
            ]
        response = client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_malformed_json": call.function.arguments}
            calls.append(ToolCall(call.id, call.function.name, arguments))
        raw_usage = response.usage
        usage = Usage(
            int(getattr(raw_usage, "prompt_tokens", 0) or 0),
            int(getattr(raw_usage, "completion_tokens", 0) or 0),
        )
        return ModelReply(message.content or "", calls, usage)


class Conversation:
    """Provider-neutral conversation with cumulative token accounting."""

    def __init__(
        self,
        backend: Backend,
        system: str,
        tools: list[dict[str, Any]] | None = None,
    ):
        self.backend = backend
        self.tools = tools
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ]
        self.usage = Usage()
        self.iterations = 0

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_result(self, call: ToolCall, output: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": output}
        )

    def send(self) -> ModelReply:
        reply = self.backend.complete(self.messages, self.tools)
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": reply.text or None,
        }
        if reply.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in reply.tool_calls
            ]
        self.messages.append(assistant)
        self.usage = self.usage + reply.usage
        self.iterations += 1
        return reply
