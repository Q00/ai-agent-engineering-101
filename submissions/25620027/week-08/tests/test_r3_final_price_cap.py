"""A seller's final price cannot bypass the actual policy cap or order budget."""

from __future__ import annotations

import anyio
import httpx2
import pytest

from market_a2a.remote import Remote, Task
from r3.buyer import Choice, UnfitUnitError, negotiate
from r3.market_files import Order, ShopEntry
from r3.policy import BuyerPolicy


@pytest.mark.parametrize(
    ("kind", "cap_ratio", "budget", "ask"),
    [
        ("food", 0.58, 100000, 62000),
        ("goods", 0.62, 100000, 63000),
        ("goods", 0.68, 61000, 63000),
    ],
)
def test_final_price_above_actual_cap_retries_without_a_binding_offer(
    kind: str,
    cap_ratio: float,
    budget: int,
    ask: int,
) -> None:
    task = Task.model_validate(
        {
            "id": "t",
            "contextId": "c",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {
                    "parts": [
                        {"text": f"Final price: {ask}"},
                        {
                            "data": {
                                "cert": {
                                    "cert": {"unit": "u", "sku": "sku", "grade": "A"}
                                },
                                "warranty_hours": 72,
                            }
                        },
                    ],
                    "metadata": {"ask": ask},
                },
            },
        }
    )
    choice = Choice(
        ShopEntry(name="peer", url="http://peer.test"),
        "sku",
        "item",
        100000,
        "I want to buy item (sku)",
        0.3,
    )
    policy = BuyerPolicy(
        max_price_ratio=cap_ratio,
        first_offer_ratio=0.42,
        quick_accept_ratio=0.58,
        surplus_score=False,
    )
    order = Order.model_validate({"order": "o", "kind": kind, "acceptable": ["sku"]})
    sent: list[str] = []

    def unexpected(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        return httpx2.Response(500)

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(unexpected)
        ) as client:
            with pytest.raises(UnfitUnitError, match="final price"):
                await negotiate(
                    Remote(client, lambda _: None),
                    "http://peer.test/rpc",
                    choice,
                    budget,
                    policy=policy,
                    emit=lambda _: None,
                    order=order,
                    opened=task,
                )

    anyio.run(scenario)
    assert not sent
