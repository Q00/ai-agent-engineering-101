"""The week-01 loop as a private, bearer-authenticated MCP negotiation host."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from openai import AsyncOpenAI

from prompts import system_prompt, turn_prompt


class ModelCallError(RuntimeError):
    """Only safe metadata; provider exception messages can contain credentials."""

    def __init__(self, error_type, status_code=None):
        self.error_type = error_type
        self.status_code = status_code
        super().__init__(f"{error_type};status={status_code}")


class EmptyChoicesError(Exception):
    pass


class OpenAIBackend:
    def __init__(self, model="gpt-5.4-mini", temperature=0.2,
                 reasoning_effort="none", max_completion_tokens=500,
                 max_retries=4, request_timeout=90.0, backoff_base=1.0,
                 *, client=None, sleeper=asyncio.sleep):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.backoff_base = backoff_base
        self.base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        parts = urlsplit(self.base_url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("model base URL must not contain credentials or query parameters")
        self.provider = "openrouter" if parts.hostname == "openrouter.ai" else "openai-compatible"
        self._client = client
        self._sleep = sleeper
        self.calls = 0

    def configuration(self):
        return {"model": self.model, "temperature": self.temperature,
                "reasoning_effort": self.reasoning_effort,
                "max_completion_tokens": self.max_completion_tokens,
                "max_retries": self.max_retries, "request_timeout": self.request_timeout,
                "backoff_base": self.backoff_base, "base_url": self.base_url,
                "provider": self.provider, "sdk_retries": 0, "seed": None}

    async def __aenter__(self):
        if self._client is None:
            self._client = AsyncOpenAI(base_url=self.base_url,
                                       timeout=self.request_timeout, max_retries=0)
        return self

    async def close(self):
        if self._client is not None:
            await self._client.close()

    async def __aexit__(self, *exc):
        await self.close()

    async def complete(self, messages, tools, log):
        kwargs = {"model": self.model, "messages": messages, "tools": tools,
                  "temperature": self.temperature,
                  "max_completion_tokens": self.max_completion_tokens,
                  "parallel_tool_calls": False}
        if self.provider == "openrouter":
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        else:
            kwargs["reasoning_effort"] = self.reasoning_effort
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            log({"event": "model_call", "attempt": attempt + 1,
                 "model": self.model, "message_count": len(messages)})
            try:
                response = await self._client.chat.completions.create(**kwargs)
                if not getattr(response, "choices", None):
                    raise EmptyChoicesError()
                message = response.choices[0].message
                usage = getattr(response, "usage", None)
                log({"event": "model_result", "response_model": getattr(response, "model", None),
                     "finish_reason": response.choices[0].finish_reason,
                     "message": message.model_dump(mode="json", exclude_none=True),
                     "usage": {key: getattr(usage, key, None) for key in
                               ("prompt_tokens", "completion_tokens", "total_tokens")}})
                return message
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retryable = (isinstance(exc, EmptyChoicesError) or
                             status in {408, 409, 429} or
                             (isinstance(status, int) and 500 <= status <= 599) or
                             type(exc).__name__ in {"APIConnectionError", "APITimeoutError"})
                log({"event": "model_error", "attempt": attempt + 1,
                     "error_type": type(exc).__name__, "status_code": status,
                     "retryable": retryable})
                if not retryable or attempt >= self.max_retries:
                    raise ModelCallError(type(exc).__name__, status) from None
                delay = min(self.backoff_base * (2 ** attempt), 30.0)
                log({"event": "model_retry", "delay_seconds": delay})
                await self._sleep(delay)
        raise AssertionError("unreachable")


def to_openai(tool):
    return {"type": "function", "function": {"name": tool.name,
            "description": tool.description or "", "parameters": tool.input_schema}}


async def run_turn(server_url, token, negotiation_id, role, item, limit,
                   backend, log, max_model_rounds=8):
    """Fresh private messages each turn; no condition, opponent limit or admin token.

    Only a successful move ends this host run. Refused moves return to the same
    model loop, and their reasons remain observations available for correction.
    """
    initial_calls = backend.calls
    messages = [{"role": "system", "content": system_prompt(role, item, limit)},
                {"role": "user", "content": turn_prompt(negotiation_id)}]
    log({"event": "host_start", "role": role, "negotiation_id": negotiation_id,
         "system_prompt": messages[0]["content"], "user_prompt": messages[1]["content"],
         "max_model_rounds": max_model_rounds})
    tool_calls = 0
    moved = False
    reason = "max_model_rounds"
    # The bearer credential is kept in transport headers, never in model input.
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                  timeout=60.0, trust_env=False) as http:
        async with Client(streamable_http_client(server_url, http_client=http)) as mcp:
            listed = (await mcp.list_tools()).tools
            tools = [to_openai(tool) for tool in listed]
            move_names = {t.name for t in listed
                          if t.annotations is not None and t.annotations.read_only_hint is False}
            if not move_names:
                raise ValueError("market must mark move tools with readOnlyHint=false")
            digest = hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()
            log({"event": "tools_list", "tools": tools, "sha256": digest,
                 "protocol_version": mcp.protocol_version})
            for _ in range(max_model_rounds):
                message = await backend.complete(messages, tools, log)
                messages.append(message.model_dump(mode="json", exclude_none=True))
                if not message.tool_calls:
                    reason = "model_finished_without_move"
                    break
                for call in message.tool_calls:
                    try:
                        args = json.loads(call.function.arguments)
                        if not isinstance(args, dict):
                            raise ValueError("tool arguments must be an object")
                    except (json.JSONDecodeError, ValueError):
                        log({"event": "host_argument_error", "call_id": call.id,
                             "name": call.function.name, "raw_arguments": call.function.arguments})
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": "tool error: arguments must be a JSON object"})
                        continue
                    tool_calls += 1
                    log({"event": "tool_call", "name": call.function.name,
                         "arguments": args, "call_id": call.id})
                    result = await mcp.call_tool(call.function.name, args)
                    log({"event": "tool_result", "name": call.function.name,
                         "call_id": call.id, "result": result.model_dump(mode="json", by_alias=True)})
                    out = "\n".join(c.text for c in result.content if c.type == "text")
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": ("tool error: " if result.is_error else "") + out})
                    if call.function.name in move_names and not result.is_error:
                        moved, reason = True, "successful_move"
                        break
                if moved:
                    break
    result = {"moved": moved, "reason": reason, "tool_calls": tool_calls,
              "model_calls": backend.calls - initial_calls}
    log({"event": "host_end", **result})
    return result
