"""Exact market orders reject product substitution before making an offer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from market_a2a.buyer import buy
from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.memory import Memory
from tests.market_fakes import ScriptedHarness, ShopTransport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def substitution(monkeypatch: pytest.MonkeyPatch) -> ShopTransport:
    """Serve an explicitly advertised chair regardless of the requested item."""
    peer = ShopTransport()
    peer.receipt["item"] = "chair"

    def respond(request: httpx2.Request) -> httpx2.Response:
        reply = peer.handle(request)
        if request.method == "POST":
            envelope = TypeAdapter(dict[str, JsonValue]).validate_json(reply.content)
            result = envelope["result"]
            assert isinstance(result, dict)
            task = result["task"]
            assert isinstance(task, dict)
            task["metadata"] = {"item": "chair"}
            return httpx2.Response(200, json=envelope)
        return reply

    def create_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond))

    monkeypatch.setattr("market_a2a.buyer.create_client", create_client)
    return peer


def test_market_order_when_seller_substitutes_product_never_offers(
    tmp_path: Path, substitution: ShopTransport
) -> None:
    # Given: a lamp order faces a seller advertising a chair and a chair receipt.
    shops_path = tmp_path / "shops.json"
    _ = shops_path.write_text(
        '[{"name":"Substituting Shop","url":"http://shop.test","reviews":[]}]',
        encoding="utf-8",
    )
    memory_path = tmp_path / "memory.json"
    harness = ScriptedHarness(
        [
            ("select_shop", {"index": 0, "reason": "Only available shop"}),
            (
                "leave_review",
                {"stars": 2, "review": "No purchase; order was rejected."},
            ),
        ]
    )
    # When: the market buyer observes the substituted product identity.
    result = anyio.run(
        market_buy,
        harness,
        MarketRequest(shops_path, "lamp", 120, memory_path),
        print,
    )
    # Then: only the initial item request was sent, with no price offer or receipt.
    posts = [request for request in substitution.requests if request.method == "POST"]
    assert len(posts) == 1
    assert json.loads(posts[0].content)["params"]["message"]["parts"] == [
        {"text": "I want to buy lamp"}
    ]
    assert result.receipt is None
    remembered = Memory.load(memory_path).shops["Substituting Shop"][0]
    assert remembered.item == "lamp"
    assert remembered.purchase.completed is False
    assert remembered.purchase.failure_observation == (
        "Purchase failed: OrderedItemMismatchError"
    )


def test_descriptive_order_when_seller_resolves_item_preserves_legacy_buy(
    substitution: ShopTransport,
) -> None:
    # Given: the existing buyer asks descriptively and the seller resolves a chair.
    harness = ScriptedHarness(
        [("offer", {"price": 120, "reason": "The resolved chair is suitable"})]
    )
    # When: the existing Week 6 interface negotiates the descriptive request.
    result = anyio.run(
        buy, harness, "http://shop.test", "a comfortable seat", 120, print
    )
    # Then: the resolved product can still be bought through the original interface.
    assert result.completed is True
    assert result.item == "chair"
    assert len(substitution.requests) == 3
