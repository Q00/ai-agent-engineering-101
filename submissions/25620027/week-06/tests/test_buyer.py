# Copyright (c) 2026 25620027
"""Exercise the real OpenAI SDK against deterministic HTTP responses."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import ClassVar, Final, Literal

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, ValidationError

from buyer import BuyerConversation, BuyerDecisionError

MODEL: Final = "gpt-4.1-nano-2025-04-14"
CARDS: Final = (
    '{"name":"Birch","description":"Furniture."}',
    '{"name":"Juniper","description":"Furniture."}',
)
PICK: Final = '{"seller":"Birch","reason":"Suitable furniture."}'
OFFER: Final = '{"action":"offer","amount":999,"reason":"Accept the quote."}'


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class WireMessage(BaseModel):
    """Typed role and content observed on the request wire."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str


class WireRequest(BaseModel):
    """Decision request fields checked by contract tests."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    model: str
    temperature: float
    messages: tuple[WireMessage, ...]


@dataclass(frozen=True, slots=True)
class Wire:
    """Mutable request/event lists intentionally capture the HTTP boundary."""

    replies: tuple[str, ...] = (PICK, OFFER, OFFER)
    requests: list[WireRequest] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    status: int = 200

    def respond(self, request: httpx.Request) -> httpx.Response:
        """Serve a prepared response while capturing the SDK request body."""
        self.requests.append(WireRequest.model_validate_json(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "limited"}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": self.replies[len(self.requests) - 1],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )


@asynccontextmanager
async def conversation(wire: Wire) -> AsyncGenerator[BuyerConversation]:
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(wire.respond)) as http,
        AsyncOpenAI(api_key="fake-test-key", http_client=http, max_retries=0) as client,
    ):
        yield BuyerConversation(client, MODEL, 0, "lamp", 173, wire.events.append)


@pytest.mark.anyio
async def test_budget_is_system_only_when_episode_continues() -> None:
    # Given: an episode already selected a seller.
    wire = Wire()
    async with conversation(wire) as buyer:
        _ = await buyer.choose(CARDS)
        # When: the next offer is requested.
        _ = await buyer.propose("Seller opening quote: 200")
    # Then: the budget appears exclusively in system-role context.
    for request in wire.requests:
        assert any(
            "173" in msg.content for msg in request.messages if msg.role == "system"
        )
        assert all(
            "173" not in msg.content for msg in request.messages if msg.role != "system"
        )


@pytest.mark.anyio
async def test_payload_keeps_same_controls_when_card_claim_changes() -> None:
    # Given: only one card's description differs between conditions.
    inflated = '{"name":"Juniper","description":"Lowest prices guaranteed."}'
    wire = Wire(replies=(PICK, PICK))
    async with conversation(wire) as plain:
        _ = await plain.choose(CARDS)
    # When: a fresh episode sees the changed card.
    async with conversation(wire) as changed:
        _ = await changed.choose((CARDS[0], inflated))
    # Then: every request value except that raw card is identical.
    before, after = wire.requests
    assert before.model == after.model == MODEL
    assert before.temperature == after.temperature == 0
    assert tuple(msg for msg in before.messages if msg.content != CARDS[1]) == tuple(
        msg for msg in after.messages if msg.content != inflated
    )


@pytest.mark.anyio
async def test_raw_cards_keep_order_without_metadata_when_selecting() -> None:
    # Given: cards arrive in reversed order.
    wire = Wire()
    cards = (CARDS[1], CARDS[0])
    async with conversation(wire) as buyer:
        # When: the buyer selects a seller.
        choice = await buyer.choose(cards)
    # Then: raw card messages retain their order and introduce no role metadata.
    messages = wire.requests[0].messages
    assert tuple(msg.content for msg in messages[2:4]) == cards
    assert not any(
        token in msg.content
        for msg in messages
        for token in ('"cheap"', '"pricey"', '"reserve"', '"condition"')
    )
    assert choice.seller == "Birch"


@pytest.mark.anyio
async def test_prior_replies_and_observations_survive_when_offer_continues() -> None:
    # Given: choice and first offer already occurred.
    wire = Wire()
    async with conversation(wire) as buyer:
        _ = await buyer.choose(CARDS)
        _ = await buyer.propose("Opening quote: 200")
        # When: a counteroffer requests a second offer.
        _ = await buyer.propose("Counteroffer: 180")
    # Then: the next model call sees both assistant decisions and observations.
    contents = tuple(message.content for message in wire.requests[2].messages)
    assert contents.index(PICK) < contents.index("Opening quote: 200")
    assert contents.index("Opening quote: 200") < contents.index(OFFER)
    assert contents.index(OFFER) < contents.index("Counteroffer: 180")


@pytest.mark.anyio
async def test_over_budget_offer_is_unchanged_when_model_proposes_it() -> None:
    # Given: a model response proposes 999 against a budget of 173.
    wire = Wire(replies=(OFFER,))
    async with conversation(wire) as buyer:
        # When: the proposal is parsed.
        result = await buyer.propose("Opening quote: 999")
    # Then: the attempted violation remains available to the runner.
    assert result.amount == 999
    assert result.action == "offer"
    assert f"BUYER_RESPONSE {OFFER}" in wire.events


@pytest.mark.anyio
async def test_usage_accumulates_when_decisions_continue() -> None:
    # Given: one selection has consumed model tokens.
    wire = Wire()
    async with conversation(wire) as buyer:
        _ = await buyer.choose(CARDS)
        # When: an offer consumes another completion.
        _ = await buyer.propose("Opening quote: 200")
        # Then: actual SDK usage and attempted-call counts accumulate.
        assert (buyer.llm_calls, buyer.input_tokens, buyer.output_tokens) == (2, 22, 14)
    assert (
        len([event for event in wire.events if event.startswith("BUYER_USAGE ")]) == 2
    )
    assert all("fake-test-key" not in event for event in wire.events)


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["not json", '{"seller":1,"reason":"bad type"}'])
async def test_parse_error_survives_when_choice_is_malformed(raw: str) -> None:
    # Given: an invalid model choice.
    wire = Wire(replies=(raw,))
    async with conversation(wire) as buyer:
        # When: choosing from valid cards.
        with pytest.raises(ValidationError):
            _ = await buyer.choose(CARDS)
    # Then: the original response remains in the capture without a fallback choice.
    assert f"BUYER_RESPONSE {raw}" in wire.events
    assert all(not event.startswith("BUYER_CHOICE ") for event in wire.events)


@pytest.mark.anyio
async def test_unknown_seller_is_error_when_name_is_outside_cards() -> None:
    # Given: a syntactically valid choice names a nonexistent seller.
    wire = Wire(replies=('{"seller":"Elsewhere","reason":"Unknown."}',))
    async with conversation(wire) as buyer:
        # When: selecting a seller.
        with pytest.raises(BuyerDecisionError):
            _ = await buyer.choose(CARDS)
    # Then: one failed call is retained without fabricating a selection.
    assert len(wire.requests) == 1
    assert all(not event.startswith("BUYER_CHOICE ") for event in wire.events)


@pytest.mark.anyio
async def test_sdk_error_propagates_when_api_returns_rate_limit() -> None:
    # Given: a rate-limited HTTP endpoint and disabled SDK retries.
    wire = Wire(status=429)
    async with conversation(wire) as buyer:
        # When: choosing a seller.
        with pytest.raises(RateLimitError):
            _ = await buyer.choose(CARDS)
        # Then: the failed API attempt is counted once and propagated.
        assert buyer.llm_calls == 1
    assert len(wire.requests) == 1


@pytest.mark.anyio
async def test_walk_away_survives_when_model_declines() -> None:
    # Given: a model decision to abandon negotiations.
    wire = Wire(
        replies=(
            json.dumps(
                {"action": "walk_away", "amount": None, "reason": "Cannot agree."}
            ),
        )
    )
    async with conversation(wire) as buyer:
        # When: deciding the next step.
        decision = await buyer.propose("Final quote: 200")
    # Then: the runner receives the cancellation decision.
    assert (decision.action, decision.amount) == ("walk_away", None)
