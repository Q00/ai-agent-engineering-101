"""Offline market flows drive the real buyer through an HTTP transport fake."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import ValidationError

from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.memory import Memory, Trade
from market_a2a.models import HarnessError, Purchase
from tests.market_fakes import ScriptedHarness, ShopTransport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> ShopTransport:
    peer = ShopTransport()

    def create_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle))

    monkeypatch.setattr("market_a2a.buyer.create_client", create_client)
    return peer


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    path = tmp_path / "shops.json"
    _ = path.write_text(
        json.dumps(
            [
                {
                    "name": "North",
                    "url": "http://north.test",
                    "reviews": [{"stars": 1, "review": "Overcharged.", "round": 1}],
                },
                {
                    "name": "South",
                    "url": "http://south.test/",
                    "reviews": [{"stars": 5, "review": "Fair receipt.", "round": 1}],
                },
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_trade_when_model_selects_shop_preserves_receipt_and_budget(
    registry: Path, tmp_path: Path, wire: ShopTransport
) -> None:
    # Given: invalid choices and an over-budget offer precede valid model actions.
    harness = ScriptedHarness(
        [
            ("select_shop", {"index": 9, "reason": "Invalid shop"}),
            ("select_shop", {"index": 1, "reason": "Specific positive trade reports"}),
            ("offer", {"price": 121, "reason": "Over budget"}),
            ("offer", {"price": 120, "reason": "Accept within budget"}),
            ("leave_review", {"stars": 9, "review": "Invalid rating"}),
            ("leave_review", {"stars": 4, "review": "Receipt matched the agreement."}),
        ]
    )
    logs: list[str] = []
    memory_path = tmp_path / "memory.json"
    # When: the real buyer performs the selected A2A negotiation.
    result = anyio.run(
        market_buy,
        harness,
        MarketRequest(registry, "lamp", 120, memory_path),
        logs.append,
    )
    # Then: only the selected shop receives authorized messages and raw data survives.
    assert result.shop == "http://south.test/"
    assert result.receipt == wire.receipt
    assert result.receipt is not None
    assert type(result.receipt["price"]) is float
    assert {request.url.host for request in wire.requests} == {"south.test"}
    posts = [request for request in wire.requests if request.method == "POST"]
    assert len(posts) == 2
    assert json.loads(posts[-1].content)["params"]["message"]["parts"] == [
        {"text": "120"}
    ]
    assert all(request.headers["A2A-Version"] == "1.0" for request in wire.requests)
    assert json.loads(posts[-1].content)["params"]["message"]["taskId"] == "trade-1"
    evaluation = Memory.load(memory_path).shops["South"][0].evaluation
    assert evaluation is not None
    assert evaluation.stars == result.stars == 3
    assert evaluation.review == result.review
    assert any(line.startswith("[MARKET SELECTION] ") for line in logs)
    assert [feedback.terminal for feedback in harness.feedback] == [
        False,
        True,
        False,
        True,
        False,
        True,
    ]


def test_memory_when_url_changes_retains_failed_trade_for_selection(
    registry: Path, tmp_path: Path, wire: ShopTransport
) -> None:
    # Given: a failed purchase is recorded under South's stable name.
    memory_path = tmp_path / "memory.json"
    first = ScriptedHarness(
        [
            ("select_shop", {"index": 1, "reason": "Try South"}),
            ("walk_away", {"reason": "No acceptable deal"}),
            ("leave_review", {"stars": 3, "review": "No purchase was made."}),
        ]
    )
    _ = anyio.run(
        market_buy, first, MarketRequest(registry, "lamp", 100, memory_path), print
    )
    _ = registry.write_text(
        '[{"name":"South","url":"http://restarted.test","reviews":[]}]',
        encoding="utf-8",
    )
    second = ScriptedHarness(
        [
            ("select_shop", {"index": 0, "reason": "Remember the previous no-deal"}),
            ("walk_away", {"reason": "Still too expensive"}),
            ("leave_review", {"stars": 3, "review": "No deal within budget."}),
        ]
    )
    # When: the same shop restarts at a different URL.
    result = anyio.run(
        market_buy, second, MarketRequest(registry, "lamp", 100, memory_path), print
    )
    # Then: its prior failed trade is present in the selection state and both persist.
    assert second.states[0] == {
        "item": "lamp",
        "budget": 100,
        "shops": [
            {
                "index": 0,
                "listing": {
                    "name": "South",
                    "url": "http://restarted.test",
                    "reviews": [],
                },
                "locally_observed_operator_directive": False,
                "own_trades": [
                    {
                        "item": "lamp",
                        "budget": 100,
                        "completed": False,
                        "price": None,
                        "receipt_present": False,
                        "operator_directive_observed": False,
                        "incident_response_count": 0,
                        "same_requested_item": True,
                        "price_within_current_budget": None,
                    }
                ],
            }
        ],
    }
    assert len(Memory.load(memory_path).shops["South"]) == 2
    assert result.receipt is None
    assert wire.requests[-1].url.host == "restarted.test"


def test_failure_when_review_model_fails_still_persists_trade(
    registry: Path, tmp_path: Path, wire: ShopTransport
) -> None:
    # Given: the review model cannot supply an action after a no-deal result.
    harness = ScriptedHarness(
        [
            ("select_shop", {"index": 0, "reason": "Try this shop"}),
            ("walk_away", {"reason": "No affordable offer"}),
        ]
    )
    memory_path = tmp_path / "memory.json"
    # When: review generation fails.
    with pytest.raises(HarnessError, match="Scripted model exhausted"):
        _ = anyio.run(
            market_buy,
            harness,
            MarketRequest(registry, "lamp", 100, memory_path),
            print,
        )
    # Then: a durable unrated failed trade remains.
    trade = Memory.load(memory_path).shops["North"][0]
    assert trade.purchase.completed is False
    assert trade.evaluation is None
    assert len(wire.requests) == 2


@pytest.mark.parametrize("invalid", ["{}", '[{"name":"x","url":"u","reviews":[{}]}]'])
def test_registry_when_schema_invalid_surfaces_error(
    tmp_path: Path, invalid: str, wire: ShopTransport
) -> None:
    # Given: malformed input cannot silently become a different registry.
    path = tmp_path / "shops.json"
    _ = path.write_text(invalid, encoding="utf-8")
    # When: the file crosses the input boundary.
    with pytest.raises(ValidationError):
        _ = anyio.run(
            market_buy,
            ScriptedHarness([]),
            MarketRequest(path, "lamp", 120, tmp_path / "memory.json"),
            print,
        )
    # Then: no remote shop receives a request.
    assert wire.requests == []


def test_memory_when_corrupt_surfaces_error(tmp_path: Path) -> None:
    # Given: a pre-existing memory file is invalid JSON.
    path = tmp_path / "memory.json"
    _ = path.write_text("corrupt", encoding="utf-8")
    # When: it is loaded.
    with pytest.raises(ValidationError):
        _ = Memory.load(path)
    # Then: its bytes remain available for repair.
    assert path.read_text(encoding="utf-8") == "corrupt"


def test_trade_when_transport_fails_records_failure_and_review(
    registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a shop's discovery request fails at the HTTP transport boundary.
    def unavailable(request: httpx2.Request) -> httpx2.Response:
        message = "Connection refused"
        raise httpx2.ConnectError(message, request=request)

    def create_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(unavailable))

    monkeypatch.setattr("market_a2a.buyer.create_client", create_client)
    harness = ScriptedHarness(
        [
            ("select_shop", {"index": 0, "reason": "Try the listed shop"}),
            ("leave_review", {"stars": 2, "review": "Could not connect; no purchase."}),
        ]
    )
    memory_path = tmp_path / "memory.json"
    # When: the buyer attempts its selected trade.
    result = anyio.run(
        market_buy,
        harness,
        MarketRequest(registry, "lamp", 120, memory_path),
        print,
    )
    # Then: a connection failure has a neutral rating and durable null receipt.
    trade = Memory.load(memory_path).shops["North"][0]
    assert result.receipt is None
    assert trade.purchase.failure_observation == "Purchase failed: ConnectError"
    assert trade.evaluation is not None
    assert trade.evaluation.stars == result.stars == 3
    assert trade.evaluation.review == result.review


def test_memory_when_atomic_replace_fails_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an existing file and a subsequent failed atomic replacement.
    path = tmp_path / "memory.json"
    Memory().save(path)
    previous = path.read_bytes()
    next_memory = Memory().append(
        "South",
        Trade(
            item="lamp",
            budget=120,
            selection_reason="Previous reports",
            purchase=Purchase(completed=False, item="lamp"),
        ),
    )

    def fail_replace(_source: Path, _target: Path) -> Path:
        message = "Simulated replacement failure"
        raise OSError(message)

    monkeypatch.setattr(type(path), "replace", fail_replace)
    # When: persisting the new snapshot cannot finish.
    with pytest.raises(OSError, match="Simulated replacement failure"):
        next_memory.save(path)
    # Then: the previous snapshot survives and no temporary file remains.
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]
