from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from time import monotonic

import anyio
import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from market_a2a.harness import MODEL, Harness
from market_a2a.model_provider import (
    FREE_MODEL,
    FREE_ROUTER,
    PROMOTION_END,
    configured_model,
    create_model_client,
    request_model,
)
from market_a2a.models import HarnessError


def test_unknown_provider_and_missing_key_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_MODEL_PROVIDER", "typo")
    with pytest.raises(HarnessError, match="AX_MODEL_PROVIDER"):
        _ = configured_model()
    monkeypatch.setenv("AX_MODEL_PROVIDER", "openrouter-free")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def scenario() -> None:
        async with httpx2.AsyncClient() as client:
            with pytest.raises(HarnessError, match="OPENROUTER_API_KEY"):
                _ = create_model_client(client)

    anyio.run(scenario)


@pytest.mark.parametrize(
    ("model", "now", "expected"),
    [
        (FREE_MODEL, PROMOTION_END - timedelta(seconds=1), FREE_MODEL),
        (FREE_MODEL, PROMOTION_END, FREE_ROUTER),
        (FREE_MODEL, PROMOTION_END + timedelta(days=5), FREE_ROUTER),
        (MODEL, datetime(2026, 10, 28, tzinfo=UTC), MODEL),
    ],
)
def test_expired_promotion_uses_only_free_router(
    model: str, now: datetime, expected: str,
) -> None:
    assert request_model(model, now) == expected


def test_missing_free_endpoint_falls_back_only_to_free_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_MODEL_PROVIDER", "openrouter-free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-openrouter")
    requested: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        route = TypeAdapter(str).validate_python(body["model"])
        requested.append(route)
        assert body["provider"] == {
            "require_parameters": True,
            "max_price": {"prompt": 0, "completion": 0, "request": 0},
        }
        if route == FREE_MODEL:
            return httpx2.Response(404, json={"error": {"message": "Free retired"}})
        assert route == FREE_ROUTER
        return httpx2.Response(200, json={
            "id": "free-router", "object": "chat.completion", "created": 0,
            "model": "offline-free-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2},
        })

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
            create_model_client(http) as client,
        ):
            harness = Harness(client, print)
            _ = await harness.complete([{"role": "user", "content": "test"}], [])
            assert requested == [FREE_MODEL, FREE_ROUTER]
            assert harness.calls == 1

    anyio.run(scenario)


def test_free_model_requires_matching_endpoint() -> None:
    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as client:
            with pytest.raises(HarnessError, match="OpenRouter endpoint"):
                _ = Harness(client, print, model=FREE_MODEL)
        async with AsyncOpenAI(
            api_key="offline-placeholder", base_url="https://openrouter.ai/api/v1"
        ) as client:
            with pytest.raises(HarnessError, match="only the explicit free model"):
                _ = Harness(client, print, model="minimax/minimax-m3.1-flash")

    anyio.run(scenario)


@pytest.mark.parametrize("provider", ["openai", "openrouter-free"])
def test_sdk_request_uses_correct_host_model_and_zero_price_constraint(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AX_MODEL_PROVIDER", provider)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-openai")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-openrouter")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    observed: list[dict[str, JsonValue]] = []
    request_times: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        observed.append(body)
        request_times.append(monotonic())
        if provider == "openrouter-free":
            assert request.url.host == "openrouter.ai"
            assert request.headers["authorization"] == "Bearer offline-openrouter"
            assert body["model"] == FREE_MODEL
            assert body["provider"] == {
                "require_parameters": True,
                "max_price": {"prompt": 0, "completion": 0, "request": 0},
            }
            assert body["max_tokens"] == 4096
            assert body["reasoning"] == {"effort": "low"}
            assert "parallel_tool_calls" not in body
            assert "max_completion_tokens" not in body
            assert "models" not in body
            assert body["tool_choice"] == "auto"
        else:
            assert request.url.host == "api.openai.com"
            assert request.headers["authorization"] == "Bearer offline-openai"
            assert body["model"] == MODEL
            assert body["max_completion_tokens"] == 450
            assert body["parallel_tool_calls"] is False
            assert "provider" not in body
            assert body["tool_choice"] == "required"
        return httpx2.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [{
                            "id": "offline-tool", "type": "function",
                            "function": {"name": "accept", "arguments": "{}"},
                        }],
                    },
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15},
            },
        )

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
            create_model_client(http) as client,
        ):
            harness = Harness(client, print)
            result = await harness.complete(
                [{"role": "user", "content": "Offline tool serialization check"}],
                [{"type": "function", "function": {"name": "accept"}}],
            )
            assert result.tool_calls
            assert harness.calls == 1
            assert harness.input_tokens == 10
            assert harness.output_tokens == 5
            assert len(observed) == 1
            assert json.dumps(observed)
            if provider == "openrouter-free":
                _ = await harness.complete(
                    [{"role": "user", "content": "Second offline request"}],
                    [{"type": "function", "function": {"name": "accept"}}],
                )
                assert len(request_times) == 2
                assert request_times[1] - request_times[0] >= 3

    anyio.run(scenario)
