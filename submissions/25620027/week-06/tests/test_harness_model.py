"""Observe model selection through the actual OpenAI HTTP serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest
import uvicorn
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel

import public_seller
from market_a2a.harness import Harness

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ModelRequest(BaseModel, frozen=True):
    """Read the model routing field at the HTTP boundary."""

    model: str


@pytest.mark.anyio
@pytest.mark.parametrize("override", [None, "liquid/lfm-2.5-2.6b:free"])
async def test_model_request_when_default_or_public_override(
    override: str | None,
) -> None:
    # Given the actual SDK client with an HTTP-level response fixture.
    requests: list[ModelRequest] = []
    logs: list[str] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        requests.append(ModelRequest.model_validate_json(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "model-selection",
                "object": "chat.completion",
                "created": 0,
                "model": requests[-1].model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "selected"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)) as http,
        AsyncOpenAI(api_key="test-only", http_client=http, max_retries=0) as client,
    ):
        harness = (
            Harness(client, logs.append)
            if override is None
            else Harness(client, logs.append, model=override)
        )
        # When one completion is serialized through the real SDK.
        reply = await harness.complete([{"role": "user", "content": "choose"}], [])

    # Then model routing and accounting identify the actual requested model.
    expected = override or "gpt-4.1-nano-2025-04-14"
    assert [request.model for request in requests] == [expected]
    assert logs == [f"MODEL_USAGE model={expected} input=1 output=1"]
    assert reply.content == "selected"


@pytest.mark.anyio
@pytest.mark.parametrize("trailing_slash", ["", "/"])
async def test_public_serve_when_paid_environment_is_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trailing_slash: str
) -> None:
    # Given independent paid experiment credentials and explicit public settings.
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-paid")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_ORG_ID", "test-only-paid-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "test-only-paid-project")
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    settings = public_seller.Settings.model_validate(
        {
            "public_url": "https://seller.example/",
            "budget_path": tmp_path / "budget.sqlite",
            "model_base_url": "https://openrouter.ai/api/v1" + trailing_slash,
            "model": "liquid/lfm-2.5-2.6b:free",
            "model_api_key": "test-only-public",
        }
    )
    harnesses: list[Harness] = []
    requests: list[httpx2.Request] = []
    build_app = public_seller.public_app

    def observe_app(config: public_seller.Settings, harness: Harness) -> FastAPI:
        harnesses.append(harness)
        return build_app(config, harness)

    def upstream(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            302, headers={"location": "https://api.openai.com/v1/chat/completions"}
        )

    def model_client(*, model: bool = False) -> httpx2.AsyncClient:
        assert model
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(upstream), follow_redirects=True
        )

    async def serve_one(_server: uvicorn.Server) -> None:
        # A redirect is an API error; the public service must never follow it.
        with pytest.raises(APIStatusError):
            _ = await harnesses[0].complete([{"role": "user", "content": "choose"}], [])

    monkeypatch.setattr(public_seller, "public_app", observe_app)
    monkeypatch.setattr(public_seller, "create_client", model_client)
    monkeypatch.setattr(uvicorn.Server, "serve", serve_one)
    # When the real public service creates its client and sends a model request.
    await public_seller.serve(settings)
    # Then only the explicit public provider receives its token and model.
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-only-public"
    assert request.headers.get("openai-organization", "") == ""
    assert request.headers.get("openai-project", "") == ""
    assert ModelRequest.model_validate_json(request.content).model == settings.model


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    ["authorization: Bearer test-only-paid", "X-Private-Token: test-only-secret"],
)
async def test_public_serve_when_custom_headers_are_present(
    monkeypatch: pytest.MonkeyPatch, headers: str
) -> None:
    # Given ambient custom headers that may contain unrelated credentials.
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", headers)
    settings = public_seller.Settings.model_validate(
        {
            "public_url": "https://seller.example/",
            "model_base_url": "https://openrouter.ai/api/v1",
            "model": "liquid/lfm-2.5-2.6b:free",
            "model_api_key": "test-only-public",
        }
    )

    def forbidden_client(*, model: bool = False) -> httpx2.AsyncClient:
        pytest.fail(f"Unexpected model HTTP client creation: {model}")

    monkeypatch.setattr(public_seller, "create_client", forbidden_client)
    # When public startup sees any ambient custom headers.
    # Then it rejects configuration before opening an outbound HTTP client.
    with pytest.raises(ValueError, match="OPENAI_CUSTOM_HEADERS") as caught:
        await public_seller.serve(settings)
    assert headers not in str(caught.value)
