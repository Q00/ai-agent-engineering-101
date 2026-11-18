"""Public discovery and persistent outbound budget without external API calls."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from market_a2a.harness import Harness
from market_a2a.remote import Card, Response
from public_seller import BudgetExhaustedError, DailyBudget, Settings, public_app

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "public_url": "https://seller.example/",
            "budget_path": tmp_path / "budget",
            "model_base_url": "https://openrouter.ai/api/v1",
            "model": "liquid/lfm-2.5-2.6b:free",
            "model_api_key": "test-only-public",
        }
    )


@pytest.mark.anyio
async def test_public_discovery_when_configured(settings: Settings) -> None:
    # Given the actual seller with a public HTTPS endpoint and no API transport.
    async with AsyncOpenAI(api_key="test-only", max_retries=0) as model:
        web = public_app(settings, Harness(model, lambda _: None))
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=web), base_url="http://container/"
        ) as client:
            # When a remote buyer discovers the seller over HTTP.
            reply = await client.get(".well-known/agent-card.json")
            # Then the card points to HTTPS ingress rather than container loopback.
            assert reply.status_code == 200
            assert Card.model_validate_json(reply.content).endpoint() == (
                "https://seller.example/"
            )


@pytest.mark.anyio
async def test_public_root_when_browser_or_rpc_client_connects(
    settings: Settings,
) -> None:
    # Given the deployment wrapper around the actual SDK routes.
    async with AsyncOpenAI(api_key="test-only", max_retries=0) as model:
        web = public_app(settings, Harness(model, lambda _: None))
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=web), base_url="http://container/"
        ) as client:
            # When browser GET and unsupported RPC POST requests share the root.
            landing = await client.get("/")
            rpc = await client.post(
                "/",
                headers={"A2A-Version": "1.0"},
                json={"jsonrpc": "2.0", "id": "probe", "method": "Unknown"},
            )
            # Then HTML discovery and SDK protocol errors remain distinct.
            assert landing.status_code == 200
            assert landing.headers["content-type"].startswith("text/html")
            assert Response.model_validate_json(rpc.content).error is not None


@pytest.mark.parametrize(
    "url",
    ["http://seller.example/", "https://user:pass@seller.example/", "https://x/?a=b"],
)
def test_settings_when_public_url_is_invalid(url: str, settings: Settings) -> None:
    # Given an invalid deployment address.
    values = settings.model_dump()
    values["public_url"] = url
    # When configuration is parsed, then startup is rejected.
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(values)


@pytest.mark.parametrize("field", ["model_base_url", "model", "model_api_key"])
def test_settings_when_public_model_config_is_missing(
    field: str, settings: Settings
) -> None:
    # Given public configuration missing one required model setting.
    values = settings.model_dump()
    del values[field]
    # When parsed, then the server cannot start with a paid/default fallback.
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_base_url", "https://api.openai.com/v1"),
        ("model_base_url", "https://api.cloudflare.com/client/v4/accounts/x/ai/v1"),
        ("model_base_url", "http://openrouter.ai/api/v1"),
        ("model_base_url", "https://openrouter.ai.evil.example/api/v1"),
        ("model_base_url", "https://user:secret@openrouter.ai/api/v1"),
        ("model_base_url", "https://openrouter.ai:443/api/v1"),
        ("model_base_url", "https://openrouter.ai/api/v1?model=paid"),
        ("model_base_url", "https://openrouter.ai/api/v1#fragment"),
        ("model_base_url", "https://openrouter.ai/api/v1/chat/completions"),
        ("model_base_url", "https://openrouter.ai/api/v1\n"),
        ("model", "gpt-4.1-nano-2025-04-14"),
        ("model", "@cf/zai-org/glm-4.7-flash"),
        ("model", "openai/gpt-4.1-nano"),
        ("model", "liquid/lfm-2.5-2.6b"),
        ("model", "auto"),
        ("model", "openrouter/auto"),
        ("model", "openrouter/free"),
        ("model", "openrouter/auto:free"),
        ("model", "openrouter/free:free"),
        ("model", "/model:free"),
        ("model", "namespace/:free"),
        ("model", "namespace/model:free:online"),
        ("model", "namespace/model:free?plugins=web"),
        ("model", "namespace/model:free\n"),
        ("model_api_key", ""),
        ("model_api_key", " \n"),
    ],
)
def test_settings_when_public_model_config_is_invalid(
    field: str, value: str, settings: Settings
) -> None:
    # Given an unsupported provider, malformed model, or empty credential.
    values = settings.model_dump()
    values[field] = value
    # When parsed, then startup fails closed.
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(values)


@pytest.mark.parametrize("field", ["plugins", "provider", "query"])
def test_settings_when_extra_routing_fields_are_present(
    field: str, settings: Settings
) -> None:
    # Given an attempted add-on or routing override outside the fixed contract.
    values = settings.model_dump()
    values[field] = "override"
    # When configuration is parsed, then unknown options fail closed.
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(values)


@pytest.mark.anyio
async def test_default_budget_when_fifty_requests_are_sent(settings: Settings) -> None:
    # Given the public default allowance and an actual HTTP pre-send budget hook.
    sent: list[str] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url.path)
        return httpx2.Response(200)

    budget = DailyBudget(settings.budget_path, settings.daily_requests)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(upstream),
        event_hooks={"request": [budget.before_request]},
    ) as client:
        for _ in range(50):
            _ = await client.post("https://openrouter.ai/api/v1/chat/completions")
        # When a fifty-first request tries to leave the service.
        # Then the default daily cap blocks it before upstream transport.
        with pytest.raises(BudgetExhaustedError):
            _ = await client.post("https://openrouter.ai/api/v1/chat/completions")
    assert len(sent) == 50


def test_settings_error_when_raw_token_is_present(settings: Settings) -> None:
    # Given environment-like input with a raw token and a missing companion field.
    values = settings.model_dump()
    values["model_api_key"] = "test-only-hidden-token"
    del values["model"]
    # When startup validation fails.
    with pytest.raises(ValidationError) as caught:
        _ = Settings.model_validate(values)
    # Then exception rendering does not reveal the raw credential dictionary.
    assert "test-only-hidden-token" not in str(caught.value)
    assert "test-only-public" not in repr(settings)


@pytest.mark.anyio
async def test_budget_when_concurrent_requests_exceed_allowance(tmp_path: Path) -> None:
    # Given 20 concurrent requests sharing a persistent allowance of 7.
    budget = DailyBudget(tmp_path / "budget.sqlite", 7)
    sent: list[str] = []
    blocked: list[str] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url.path)
        return httpx2.Response(200)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(upstream),
        event_hooks={"request": [budget.before_request]},
    ) as client:

        async def send() -> None:
            try:
                _ = await client.post("https://model.example/chat/completions")
            except BudgetExhaustedError:
                blocked.append("blocked")

        # When requests race at the real HTTP pre-send boundary.
        async with anyio.create_task_group() as group:
            for _ in range(20):
                _ = group.start_soon(send)

    # Then exactly the allowance reaches the upstream transport.
    assert len(sent) == 7
    assert len(blocked) == 13


def test_budget_when_process_restarts(tmp_path: Path) -> None:
    # Given one request already persisted by a previous process.
    path = tmp_path / "budget.sqlite"
    previous = subprocess.run(  # noqa: S603 - fixed interpreter and local test code.
        [
            sys.executable,
            "-c",
            """from public_seller import DailyBudget
from pathlib import Path
from datetime import date
import sys
DailyBudget(Path(sys.argv[1]), 1).consume(date(2026, 10, 19))""",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert previous.returncode == 0
    # When a fresh budget object in this process uses the same UTC date.
    # Then the persisted allowance prevents another request.
    with pytest.raises(BudgetExhaustedError):
        DailyBudget(path, 1).consume(date(2026, 10, 19))


def test_budget_when_utc_day_changes(tmp_path: Path) -> None:
    # Given yesterday's exhausted allowance.
    budget = DailyBudget(tmp_path / "budget.sqlite", 1)
    budget.consume(date(2026, 10, 19))
    # When the next UTC date begins.
    budget.consume(date(2026, 10, 20))
    # Then exactly one request is allowed on the new day.
    with pytest.raises(BudgetExhaustedError):
        budget.consume(date(2026, 10, 20))


@pytest.mark.anyio
async def test_budget_when_upstream_fails(tmp_path: Path) -> None:
    # Given one permitted attempt whose upstream response fails.
    budget = DailyBudget(tmp_path / "budget.sqlite", 1)
    sent: list[str] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url.path)
        return httpx2.Response(503)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(upstream),
        event_hooks={"request": [budget.before_request]},
    ) as client:
        first = await client.post("https://model.example/chat/completions")
        assert first.status_code == 503
        # When the client attempts the failed request again.
        # Then failed requests are still charged before further upstream calls.
        with pytest.raises(BudgetExhaustedError):
            _ = await client.post("https://model.example/chat/completions")
    assert len(sent) == 1
