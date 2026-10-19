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


@pytest.mark.anyio
async def test_public_discovery_when_configured(tmp_path: Path) -> None:
    # Given the actual seller with a public HTTPS endpoint and no API transport.
    settings = Settings.model_validate(
        {"public_url": "https://seller.example/", "budget_path": tmp_path / "budget"}
    )
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
async def test_public_root_when_browser_or_rpc_client_connects() -> None:
    # Given the deployment wrapper around the actual SDK routes.
    settings = Settings.model_validate({"public_url": "https://seller.example/"})
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
def test_settings_when_public_url_is_invalid(url: str) -> None:
    # Given an invalid deployment address.
    # When configuration is parsed, then startup is rejected.
    with pytest.raises(ValidationError):
        _ = Settings.model_validate({"public_url": url})


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
