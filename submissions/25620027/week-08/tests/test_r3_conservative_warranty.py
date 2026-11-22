"""Unstated warranties must not create an optimistic binding goods offer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest

from market_a2a.remote import Remote, Task
from r3 import buyer
from r3.buyer import Choice, UnfitUnitError, negotiate
from r3.market_files import Order, ShopEntry
from r3.policy import BuyerPolicy

if TYPE_CHECKING:
    from pydantic import JsonValue


@pytest.fixture(autouse=True)
def pre_patch_36_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the list-discount line; patch 3.6 is in test_r3_margin_line."""
    monkeypatch.setattr(buyer, "MARGIN_LINE", None)


def _quote(hours: int | None) -> Task:
    data: dict[str, JsonValue] = {"cert": {"cert": {"grade": "A"}}}
    if hours is not None:
        data["warranty_hours"] = hours
    return Task.model_validate(
        {
            "id": "t",
            "contextId": "c",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {
                    "parts": [{"text": "Final price: 66000"}, {"data": data}],
                    "metadata": {"ask": 66000},
                },
            },
        }
    )


def _choice() -> Choice:
    return Choice(
        ShopEntry(name="peer", url="http://peer.test"),
        "sku",
        "item",
        100000,
        "buy item (sku)",
        0.3,
    )


def _policy() -> BuyerPolicy:
    return BuyerPolicy(
        max_price_ratio=0.70,
        first_offer_ratio=0.48,
        quick_accept_ratio=0.64,
        min_expected_credit=0.30,
        surplus_score=False,
    )


@pytest.mark.parametrize("shop_known", [True, False])
def test_unstated_warranty_cannot_accept_a_price_only_linear_protection_passes(
    *,
    shop_known: bool,
) -> None:
    sent: list[str] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        return httpx2.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "task": {
                        "id": "t",
                        "contextId": "c",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "message": {
                                "parts": [{"text": "Deal"}],
                            },
                        },
                    }
                },
            },
        )

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(reply)) as client:
            with pytest.raises(UnfitUnitError, match="final price"):
                await negotiate(
                    Remote(client, lambda _: None),
                    "http://peer.test/rpc",
                    _choice(),
                    100000,
                    policy=_policy(),
                    emit=lambda _: None,
                    order=Order.model_validate(
                        {"order": "o", "kind": "goods", "acceptable": ["sku"]}
                    ),
                    opened=_quote(None),
                    warranty_odds=(
                        {"http://peer.test": 0.5} if shop_known else {},
                        0.5,
                    ),
                )

    anyio.run(scenario)
    assert not sent


def test_explicit_warranty_keeps_the_actual_promised_protection() -> None:
    sent: list[str] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        return httpx2.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "task": {
                        "id": "t",
                        "contextId": "c",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "message": {
                                "parts": [{"text": "Deal"}],
                            },
                        },
                    }
                },
            },
        )

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(reply)) as client:
            _, price = await negotiate(
                Remote(client, lambda _: None),
                "http://peer.test/rpc",
                _choice(),
                100000,
                policy=_policy(),
                emit=lambda _: None,
                order=Order.model_validate(
                    {"order": "o", "kind": "goods", "acceptable": ["sku"]}
                ),
                opened=_quote(72),
                warranty_odds=({"http://peer.test": 0.5}, 0.5),
            )
            assert price == 66000

    anyio.run(scenario)
    assert len(sent) == 1
