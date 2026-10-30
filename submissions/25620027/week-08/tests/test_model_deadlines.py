"""Interrupt stalled model I/O while preserving quota and committed tool work."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from market_a2a.harness import Harness
from market_a2a.model_provider import FREE_ROUTER
from market_a2a.models import HarnessError, ToolResult
from market_a2a.paid_fallback import DailyQuota, OpenAIFallback

if TYPE_CHECKING:
    from pathlib import Path

    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam


async def stalled(_request: httpx2.Request) -> httpx2.Response:
    """Keep the real SDK waiting until its enclosing model budget expires."""
    await anyio.sleep_forever()
    raise AssertionError


def test_stalled_sdk_response_is_cancelled_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("market_a2a.harness.MODEL_WAIT_SECONDS", 0.02)
    logs: list[str] = []

    async def warmed_transport(request: httpx2.Request) -> httpx2.Response:
        if b'"offline-warmup"' in request.content:
            return httpx2.Response(200, json={
                "id": "warmup", "object": "chat.completion", "created": 0,
                "model": "offline-warmup", "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ready"},
                }],
            })
        return await stalled(request)

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(
                transport=httpx2.MockTransport(warmed_transport)
            ) as http,
            AsyncOpenAI(api_key="offline", max_retries=0, http_client=http) as client,
        ):
            # Given: SDK platform discovery has finished outside the I/O fault.
            _ = await client.chat.completions.create(
                model="offline-warmup", messages=[],
            )
            harness = Harness(client, logs.append)
            # When: a warmed SDK waits forever on its actual HTTP transport.
            with anyio.fail_after(1), pytest.raises(HarnessError, match="response"):
                _ = await harness.complete([], [])
            # Then: the model I/O budget cancels it and reports the right error.
            assert harness.calls == 0
            assert "MODEL_TIMEOUT stage=response" in logs

    anyio.run(scenario)


def test_timeout_after_paid_reservation_does_not_refund_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("market_a2a.harness.MODEL_WAIT_SECONDS", 0.02)

    async def unavailable(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"error": {"message": "No free route"}})

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(
                transport=httpx2.MockTransport(unavailable)
            ) as free_http,
            AsyncOpenAI(api_key="offline", max_retries=0, http_client=free_http,
                        base_url="https://openrouter.ai/api/v1") as free,
            httpx2.AsyncClient(transport=httpx2.MockTransport(stalled)) as paid_http,
            AsyncOpenAI(api_key="offline", max_retries=0,
                        http_client=paid_http) as paid,
        ):
            quota = DailyQuota(tmp_path / "quota.sqlite3", 1)
            harness = Harness(
                free, print, model=FREE_ROUTER,
                fallback=OpenAIFallback(paid, quota),
            )
            with anyio.fail_after(1), pytest.raises(HarnessError, match="response"):
                _ = await harness.complete([], [])
            with pytest.raises(HarnessError, match="limit reached"):
                _ = quota.reserve()

    anyio.run(scenario)


def test_decision_budget_is_shared_across_model_corrections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("market_a2a.harness.DECISION_WAIT_SECONDS", 0.025)
    attempts: list[int] = []

    async def invalid_response(
        _self: Harness, _messages: list[ChatCompletionMessageParam],
        _tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        attempts.append(1)
        await anyio.sleep(0.015)
        return ChatCompletionMessage.model_validate({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "bad", "type": "function", "function": {
                "name": "invalid", "arguments": "{}",
            }}],
        })

    async def reject(name: str, arguments: str) -> ToolResult:
        _ = name, arguments
        return ToolResult(data="Choose an authorized action")

    monkeypatch.setattr(Harness, "complete", invalid_response)

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline") as client:
            with anyio.fail_after(1), pytest.raises(HarnessError, match="decision"):
                _ = await Harness(client, print).run("test", {}, (), reject)
            assert len(attempts) < 6

    anyio.run(scenario)


def test_model_deadline_does_not_interrupt_a_committing_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("market_a2a.harness.DECISION_WAIT_SECONDS", 0.01)

    async def answer(
        _self: Harness, _messages: list[ChatCompletionMessageParam],
        _tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        return ChatCompletionMessage.model_validate({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "commit", "type": "function", "function": {
                "name": "commit", "arguments": "{}",
            }}],
        })

    async def commit(name: str, arguments: str) -> ToolResult:
        _ = name, arguments
        await anyio.sleep(0.03)
        return ToolResult(data="committed", terminal=True)

    monkeypatch.setattr(Harness, "complete", answer)

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline") as client:
            with anyio.fail_after(1):
                result = await Harness(client, print).run("test", {}, (), commit)
            assert result.data == "committed"

    anyio.run(scenario)
