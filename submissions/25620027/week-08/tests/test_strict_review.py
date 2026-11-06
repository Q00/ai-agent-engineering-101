"""Buyer ratings require transaction evidence, independent of seller identity."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from market_a2a.market_buyer import MarketRequest, grounded_review, market_buy
from market_a2a.memory import Memory, Review, Trade
from market_a2a.models import Purchase
from market_a2a.strict_review import trade_facts
from tests.market_fakes import ScriptedHarness, ShopTransport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("shop", ["our-shop", "competitor-shop"])
def test_completed_purchase_when_seller_identity_changes_keeps_public_cap(
    shop: str,
) -> None:
    # Given: successful receipt but no comparative price or effort measurements.
    trade = Trade(
        item="lamp",
        budget=150,
        selection_reason="Listed seller",
        purchase=Purchase(
            completed=True, item="lamp", price=120, receipt={"price": 120}, shop=shop
        ),
    )
    # When: a model proposes the maximum rating.
    review = grounded_review(trade, Review(stars=5, review="Exceptional"))
    # Then: unsupported maximum praise cannot pass the factual boundary.
    assert review.stars == 3


def test_success_without_adverse_facts_cannot_support_punitive_rating() -> None:
    # Given: the local outcome contains only a successful affordable purchase.
    trade = Trade(
        item="lamp",
        budget=150,
        selection_reason="Listed seller",
        purchase=Purchase(
            completed=True, item="lamp", price=120, receipt={"price": 120}
        ),
    )
    # When: a model proposes a punitive score with an unsupported fraud claim.
    review = grounded_review(trade, Review(stars=1, review="Fraudulent receipt"))
    # Then: no negative incident is fabricated from an ordinary completed deal.
    assert review.stars == 3
    assert "fraud" not in review.review.casefold()


@pytest.mark.parametrize("proposed_stars", [1, 2, 3, 4, 5])
def test_completed_rating_when_value_is_good_remains_below_three_point_five(
    proposed_stars: int,
) -> None:
    # Given: favorable same-item price, budget headroom and low measured effort.
    trade = Trade(
        item="lamp",
        budget=150,
        selection_reason="Observed transaction",
        purchase=Purchase(
            completed=True,
            item="lamp",
            price=120,
            receipt={"price": 120},
            offer_count=1,
        ),
    )
    # When: the model submits any valid semantic grade through its review action.
    result = grounded_review(
        trade, Review(stars=proposed_stars, review="Proposed"), (140,)
    )
    # Then: the integer public score neither exceeds 3.5 nor invents an incident.
    assert result.stars == 3
    assert type(result.stars) is int


def test_old_purchase_when_metrics_absent_keeps_unknown() -> None:
    # Given: a saved purchase predating effort telemetry.
    raw = '{"completed":true,"item":"lamp","price":120,"receipt":{"price":120}}'
    # When: memory parsing uses the new backward-compatible purchase schema.
    purchase = Purchase.model_validate_json(raw)
    # Then: missing evidence is not invented as zero offers or a seller ask.
    assert purchase.offer_count is None
    assert purchase.initial_ask is None


@pytest.mark.parametrize("historical_item", ["lamp", "chair"])
def test_market_review_when_actual_offer_and_prior_item_are_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historical_item: str
) -> None:
    # Given: own receipt history and an A2A peer with an observable initial ask.
    peer = ShopTransport()

    def respond(request: httpx2.Request) -> httpx2.Response:
        reply = peer.handle(request)
        return httpx2.Response(
            reply.status_code,
            content=reply.content.replace(
                b"The asking price is 120.", b"Counteroffer: 140."
            ),
        )

    def create_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond))

    monkeypatch.setattr("market_a2a.buyer.create_client", create_client)
    shops = tmp_path / "shops.json"
    _ = shops.write_text(
        json.dumps(
            [
                {
                    "name": "Current",
                    "url": "http://shop.test",
                    "reviews": [{"round": 1, "stars": 5, "review": "Overcharged"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    memory_path = tmp_path / "memory.json"
    old_trade = Trade(
        item=historical_item,
        budget=150,
        selection_reason="Prior own trade",
        purchase=Purchase(
            completed=True, item=historical_item, price=120, receipt={"price": 120}
        ),
        evaluation=Review(stars=5, review="Historical review preserved"),
    )
    failed_trade = Trade(
        item="lamp",
        budget=150,
        selection_reason="No deal",
        purchase=Purchase(completed=False, item="lamp", price=1),
    )
    Memory().append("Prior", old_trade).append("Prior", failed_trade).save(memory_path)
    harness = ScriptedHarness(
        [
            ("select_shop", {"index": 0, "reason": "Listed shop"}),
            ("offer", {"price": 151, "reason": "Invalid attempt"}),
            ("offer", {"price": 120, "reason": "Actual offer"}),
            ("leave_review", {"stars": 5, "review": "Unverified quality claim"}),
        ]
    )
    # When: the real buyer validates actions, negotiates and records its review.
    result = anyio.run(
        market_buy, harness, MarketRequest(shops, "lamp", 150, memory_path), print
    )
    # Then: rejected actions do not inflate measured effort or contaminate history.
    stored = Memory.load(memory_path)
    purchase = stored.shops["Current"][0].purchase
    state = TypeAdapter(dict[str, JsonValue]).validate_python(harness.states[-1])
    assert purchase.initial_ask == 140
    assert purchase.offer_count == 1
    assert state["offer_count"] == 1
    assert state["best_prior_price"] == (120 if historical_item == "lamp" else None)
    assert result.stars == 3
    assert stored.shops["Current"][-1].evaluation == Review(
        stars=result.stars, review=result.review
    )
    assert stored.shops["Prior"] == (old_trade, failed_trade)
    selection = TypeAdapter(dict[str, JsonValue]).validate_python(harness.states[0])
    listings = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
        selection["shops"]
    )
    listing = TypeAdapter(dict[str, JsonValue]).validate_python(listings[0]["listing"])
    reviews = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
        listing["reviews"]
    )
    assert "stars" not in reviews[0]
    assert reviews[0]["reviewer_reports_overcharge"] is True
    print(
        json.dumps(
            {
                "selection_input": selection,
                "review_input": state,
                "result": result.model_dump(),
            }
        )
    )


def test_selection_facts_when_own_public_rating_changes_are_identical() -> None:
    # Given: identical own trade facts with different historical public evaluations.
    trade = Trade(
        item="lamp",
        budget=150,
        selection_reason="Prior own trade",
        purchase=Purchase(
            completed=True, item="lamp", price=120, receipt={"price": 120}
        ),
        evaluation=Review(stars=1, review="Old evaluation"),
    )
    revised = trade.model_copy(
        update={"evaluation": Review(stars=5, review="Another evaluation")}
    )
    # When: historical trades cross the next purchase's selection boundary.
    before, after = trade_facts(trade), trade_facts(revised)
    # Then: own issued ratings cannot change the selection facts sent to the model.
    assert before == after
    assert before["price"] == 120
