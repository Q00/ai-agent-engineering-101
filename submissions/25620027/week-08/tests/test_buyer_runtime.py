from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from typer.testing import CliRunner

import harness_app
from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.models import HarnessError
from market_a2a.promotion import SHOP_URL
from tests.market_fakes import ScriptedHarness, ShopTransport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("own_url", [SHOP_URL, SHOP_URL + "/"])
def test_promotional_buyer_excludes_own_registered_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, own_url: str
) -> None:
    peer = ShopTransport()
    registry = tmp_path / "shops.json"
    _ = registry.write_text(json.dumps([
        {"name": "Our shop", "url": own_url, "reviews": []},
        {"name": "Other shop", "url": "http://other.test", "reviews": []},
    ]))
    harness = ScriptedHarness([
        ("select_shop", {"index": 0, "reason": "Available other shop"}),
        ("offer", {"price": 120, "reason": "Affordable"}),
        ("leave_review", {"stars": 3, "review": "Receipt matched."}),
    ])

    def create_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle))

    monkeypatch.setattr("market_a2a.buyer.create_client", create_client)

    result = anyio.run(
        market_buy, harness,
        MarketRequest(
            registry, "lamp", 120, tmp_path / "memory.json", promote_shop=True
        ),
        print,
    )

    assert result.shop == "http://other.test"
    assert result.receipt == peer.receipt
    assert {request.url.host for request in peer.requests} == {"other.test"}


def test_missing_shared_paid_ledger_blocks_market_buyer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "shops.json"
    _ = registry.write_text("[]")
    missing = tmp_path / "missing-quota.sqlite3"
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test")
    monkeypatch.setenv("AX_OPENAI_FALLBACK", "1")
    monkeypatch.setenv("AX_PAID_QUOTA_PATH", str(missing))

    result = CliRunner().invoke(harness_app.app, [
        "market-buyer", "--shops", str(registry), "--item", "lamp",
        "--budget", "120", "--promote-shop", "--mode", "model",
    ])

    assert result.exit_code != 0
    assert isinstance(result.exception, HarnessError)
    assert not missing.exists()
