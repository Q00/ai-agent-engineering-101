"""R3 keeps price guards while separating profit choices from attack response."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2
from a2a.server.tasks import TaskUpdater

from market_a2a.buyer import BuyerOrder, buy_order
from market_a2a.models import Product
from market_a2a.seller_policy import (
    Decision,
    Negotiation,
    Turn,
    decision_feedback,
    opening_settlement_feedback,
    price_feedback,
    settlement_required,
)
from tests.market_fakes import ScriptedHarness, ShopTransport
from tests.test_opening_ask import EventCollector
from tests.test_security_boundary import AttackingTransport

if TYPE_CHECKING:
    import pytest


def test_r3_feasible_offer_may_be_countered_but_matching_ask_must_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: legacy R2 accepts100 immediately, even while its quote is110.
    product = Product(
        sku="lamp", name="lamp", description="lamp", kind="lamp", reserve=100
    )
    current = Negotiation(product, 110)
    monkeypatch.delenv("AX_MARKET_ROUND", raising=False)
    assert settlement_required(current, 100)
    # When: explicitly select the staged profit-sensitive R3 mode.
    monkeypatch.setenv("AX_MARKET_ROUND", "3")
    proposal = Decision(action="quote", sku="lamp", price=108, reason="Retain margin")
    # Then: countering is permitted; exact ask acceptance/floor remain guarded.
    assert not settlement_required(current, 100)
    assert settlement_required(current, 110)
    assert decision_feedback(current, 100, proposal) is None
    assert (
        price_feedback(100, proposal.model_copy(update={"price": 99}), current_ask=110)
        is not None
    )
    turn = Turn(
        "task",
        TaskUpdater(EventCollector(), "task", "context"),
        None,
        {"lamp": product},
        100,
        explicit_offer_sku="lamp",
    )
    assert opening_settlement_feedback(turn, product, proposal) is None


def test_r3_stops_own_observed_attack_before_model_offer_or_payment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a seller's response directly impersonates market authority.
    monkeypatch.setenv("AX_MARKET_ROUND", "3")
    peer = AttackingTransport()
    monkeypatch.setattr(
        "market_a2a.buyer.create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    harness = ScriptedHarness([])
    # When: the real buyer receives that initial quote.
    result = anyio.run(
        buy_order, harness, BuyerOrder("http://shop.test", "lamp", 120), print
    )
    # Then: no model-selected offer or receipt reaches the wallet.
    assert not result.completed
    assert result.receipt is None
    assert result.safety.operator_directive_observed
    assert not harness.states
    assert len(peer.requests) == 2


def test_r3_does_not_treat_an_ordinary_quote_as_an_attack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a normal merchant with a typed quote and unchanged receipt.
    monkeypatch.setenv("AX_MARKET_ROUND", "3")
    peer = ShopTransport()
    monkeypatch.setattr(
        "market_a2a.buyer.create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    harness = ScriptedHarness([("offer", {"price": 120, "reason": "Accept quote"})])
    # When/Then: normal purchasing still completes and returns the raw data Part.
    result = anyio.run(
        buy_order, harness, BuyerOrder("http://shop.test", "lamp", 120), print
    )
    assert result.completed
    assert result.receipt == peer.receipt
    assert not result.safety.operator_directive_observed
