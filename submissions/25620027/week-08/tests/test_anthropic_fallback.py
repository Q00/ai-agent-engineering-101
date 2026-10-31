from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from openai import APIStatusError, AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from market_a2a import harness as harness_module
from market_a2a import model_session
from market_a2a.anthropic_fallback import (
    ANTHROPIC_URL,
    HAIKU_MODEL,
    AnthropicFallback,
)
from market_a2a.harness import Harness
from market_a2a.model_provider import FREE_ROUTER
from market_a2a.models import HarnessError, ToolResult, ToolSpec
from market_a2a.paid_fallback import DailyQuota, configured_paid_provider

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("status", [402, 404, 429])
def test_free_exhaustion_dispatches_real_haiku_tool_without_retry_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    # Given: the real SDK sees a free quota error and an authorized Haiku tool.
    requests: list[str] = []
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(anyio, "sleep", wait)

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.host)
        if request.url.host == "openrouter.ai":
            assert request.headers["authorization"] == "Bearer free-only"
            return httpx2.Response(status, json={"error": {"message": "offline quota"}})
        assert str(request.url) == ANTHROPIC_URL + "chat/completions"
        assert request.headers["authorization"] == "Bearer paid-only"
        body = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        assert body["model"] == HAIKU_MODEL
        assert body["max_completion_tokens"] == 450
        assert body["tool_choice"] == "required"
        assert body["parallel_tool_calls"] is False
        assert "temperature" not in body
        assert "provider" not in body
        return httpx2.Response(200, json={
            "id": "haiku", "object": "chat.completion", "created": 0,
            "model": HAIKU_MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "action", "type": "function", "function": {
                        "name": "record", "arguments": '{"value":7}',
                    },
                }],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 12,
                      "total_tokens": 22},
        })

    async def dispatch(name: str, arguments: str) -> ToolResult:
        assert name == "record"
        assert json.loads(arguments) == {"value": 7}
        return ToolResult(data=7, terminal=True)

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
            AsyncOpenAI(api_key="free-only", base_url="https://openrouter.ai/api/v1",
                        max_retries=0, http_client=http) as free,
            AsyncOpenAI(api_key="paid-only", base_url=ANTHROPIC_URL,
                        max_retries=0, http_client=http) as paid,
        ):
            quota = DailyQuota(tmp_path / "quota.sqlite3", 1)
            logs: list[str] = []
            harness = Harness(free, logs.append, model=FREE_ROUTER,
                              fallback=AnthropicFallback(paid, quota))
            # When: run the existing typed tool loop across the real SDK wire.
            result = await harness.run("offline", {}, (
                ToolSpec(name="record", description="Record a value", parameters={
                    "type": "object", "properties": {"value": {"type": "integer"}},
                    "required": ["value"], "additionalProperties": False,
                }),
            ), dispatch)
            # Then: the action commits, with one paid attempt and no retry sleep.
            assert result.data == 7
            assert requests == ["openrouter.ai", "api.anthropic.com"]
            assert all(seconds == 0 for seconds in waits)
            assert harness.input_tokens == 10
            assert any(f"route={HAIKU_MODEL}" in line for line in logs)
            with pytest.raises(HarnessError, match="limit reached"):
                _ = DailyQuota(quota.path, 1).reserve()

    anyio.run(scenario)


def test_failed_haiku_request_consumes_shared_legacy_quota(tmp_path: Path) -> None:
    # Given: buyer/seller quota already includes one pre-existing paid attempt.
    path = tmp_path / "quota.sqlite3"
    _ = DailyQuota(path, 2).reserve()
    requests: list[str] = []

    def fail(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.host)
        return httpx2.Response(500, json={"error": {"message": "offline failure"}})

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as http,
            AsyncOpenAI(api_key="offline", base_url=ANTHROPIC_URL,
                        max_retries=0, http_client=http) as client,
        ):
            fallback = AnthropicFallback(client, DailyQuota(path, 2))
            # When: the one remaining attempt fails at the HTTP boundary.
            with pytest.raises(APIStatusError):
                _ = await fallback.complete([], [], print)
            # Then: reopening the ledger cannot refund it or send another request.
            with pytest.raises(HarnessError, match="limit reached"):
                _ = await AnthropicFallback(
                    client, DailyQuota(path, 2)
                ).complete([], [], print)
            assert requests == ["api.anthropic.com"]

    anyio.run(scenario)


@pytest.mark.parametrize(("url", "model", "retries"), [
    ("https://example.test/v1/", HAIKU_MODEL, 0),
    ("http://api.anthropic.com/v1/", HAIKU_MODEL, 0),
    (ANTHROPIC_URL, "claude-opus-5-5", 0),
    (ANTHROPIC_URL, HAIKU_MODEL, 2),
])
def test_unapproved_endpoint_model_or_retry_cannot_send_paid_request(
    tmp_path: Path, url: str, model: str, retries: int,
) -> None:
    # Given: an unsupported paid configuration.
    async def scenario() -> None:
        async with AsyncOpenAI(
            api_key="offline", base_url=url, max_retries=retries,
        ) as client:
            quota = DailyQuota(tmp_path / "quota.sqlite3", 1)
            # When: create the fallback before any network I/O.
            with pytest.raises(HarnessError, match="direct endpoint"):
                _ = AnthropicFallback(client, quota, model)
            # Then: no paid reservation has been consumed.
            assert quota.reserve() == 1

    anyio.run(scenario)


@pytest.mark.parametrize(("selected", "legacy"), [
    ("anthropic", "1"), ("typo", "0"), ("none", "invalid"),
])
def test_conflicting_provider_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch, selected: str, legacy: str,
) -> None:
    # Given: ambiguous opt-ins or an unsupported provider.
    monkeypatch.setenv("AX_PAID_FALLBACK", selected)
    monkeypatch.setenv("AX_OPENAI_FALLBACK", legacy)
    # When/Then: reject it rather than silently routing a paid call.
    with pytest.raises(HarnessError):
        _ = configured_paid_provider()


def test_actual_session_wires_haiku_only_after_free_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same environment selection used by buyer and seller CLI sessions.
    quota = DailyQuota(tmp_path / "quota.sqlite3", 2)
    for key, value in {
        "AX_MODEL_PROVIDER": "openrouter-free",
        "OPENROUTER_API_KEY": "offline-free",
        "AX_PAID_FALLBACK": "anthropic",
        "AX_OPENAI_FALLBACK": "0",
        "ANTHROPIC_API_KEY": "offline-haiku",
        "ANTHROPIC_MODEL": HAIKU_MODEL,
        "AX_PAID_QUOTA_PATH": str(quota.path),
        "AX_PAID_DAILY_LIMIT": "2",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    requests: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.host)
        if request.url.host == "openrouter.ai":
            return httpx2.Response(429, json={"error": {"message": "offline quota"}})
        assert request.headers["authorization"] == "Bearer offline-haiku"
        return httpx2.Response(200, json={
            "id": "session", "object": "chat.completion", "created": 0,
            "model": HAIKU_MODEL, "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "configured"},
            }],
        })

    def client(*, model: bool = False) -> httpx2.AsyncClient:
        assert model
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(model_session, "create_client", client)

    async def scenario() -> None:
        # When: use the real session constructor and shared harness.
        async with model_session.harness_session(print) as harness:
            reply = await harness.complete([{"role": "user", "content": "test"}], [])
        # Then: no OpenAI key/host is needed and the shared ledger lost one slot.
        assert reply.content == "configured"
        assert requests == ["openrouter.ai", "api.anthropic.com"]
        assert DailyQuota(quota.path, 2).reserve() == 2

    anyio.run(scenario)


def test_paid_traffic_does_not_postpone_free_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: free quota fails once, then recovers after the fixed cooldown.
    now = [100.0]
    monkeypatch.setattr(harness_module, "monotonic", lambda: now[0])
    monkeypatch.setattr(harness_module, "FREE_CALL_INTERVAL", 0.0)
    requests: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.host)
        if len(requests) == 1:
            return httpx2.Response(429, json={"error": {"message": "offline quota"}})
        return httpx2.Response(200, json={
            "id": "cooldown", "object": "chat.completion", "created": 0,
            "model": HAIKU_MODEL if request.url.host == "api.anthropic.com"
            else "recovered-free",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
        })

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
            AsyncOpenAI(api_key="free", base_url="https://openrouter.ai/api/v1",
                        max_retries=0, http_client=http) as free,
            AsyncOpenAI(api_key="paid", base_url=ANTHROPIC_URL,
                        max_retries=0, http_client=http) as paid,
        ):
            harness = Harness(free, print, model=FREE_ROUTER,
                              fallback=AnthropicFallback(
                                  paid, DailyQuota(tmp_path / "quota.sqlite3", 3)
                              ))
            # When: requests continue during and after the original cooldown.
            for timestamp in [100.0, 120.0, 161.0]:
                now[0] = timestamp
                _ = await harness.complete([], [])
            # Then: the request at161 retries free instead of extending paid use.
            assert requests == [
                "openrouter.ai", "api.anthropic.com",
                "api.anthropic.com", "openrouter.ai",
            ]

    anyio.run(scenario)
