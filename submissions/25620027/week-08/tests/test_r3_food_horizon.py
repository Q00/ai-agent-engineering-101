"""Food lifetime stated in prose must meet the same checkout horizon."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest

from market_a2a.remote import Task
from r3 import buyer
from r3.market_files import Order
from r3.policy import BuyerPolicy
from tests.r3_stock import KIMCHI
from tests.test_r3_small_accept_guard import SmallPeers, run_small

if TYPE_CHECKING:
    from pathlib import Path


def quote(text: str) -> Task:
    """Construct the text-only task returned by the two real refused shops."""
    return Task.model_validate(
        {
            "id": "t",
            "contextId": "c",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {"parts": [{"text": text}]},
            },
        }
    )


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("shelf life left 511 h. List price 24800.", 511.0),
        ("35 h of shelf life left. Asking 10114.", 35.0),
        ("about 626.5 h shelf life left", 626.5),
        ("616.346 hours of shelf life left", 616.346),
    ],
)
def test_quote_prose_reports_remaining_hours(text: str, hours: float) -> None:
    assert buyer.stated_hours_left(quote(text), 1000) == hours


@pytest.mark.parametrize(
    ("text", "use_within"),
    [("shelf life left 511 h", 540), ("35 h of shelf life left", 36)],
)
def test_real_refusal_shapes_are_rejected_before_negotiation(
    text: str, use_within: int
) -> None:
    order = Order(
        item=KIMCHI, kind="food", acceptable=(KIMCHI,), use_within_hours=use_within
    )
    policy = BuyerPolicy(food_margin_hours=4)
    with pytest.raises(buyer.ShortShelfLifeError):
        buyer.check_shelf_life(quote(text), order, policy)
    assert buyer.accept_policy(order, policy)["min_hours_left"] == use_within + 4


def test_quote_and_checkout_round_the_same_horizon() -> None:
    order = Order(
        item=KIMCHI, kind="food", acceptable=(KIMCHI,), use_within_hours=36.004
    )
    policy = BuyerPolicy(food_margin_hours=4)
    assert buyer.accept_policy(order, policy)["min_hours_left"] == 40.0
    buyer.check_shelf_life(quote("40 h of shelf life left"), order, policy)
    assert buyer.stated_hours_left(quote("We need 40 h; asking 15000"), 1000) is None


@pytest.mark.parametrize("text", ["shelf life left 20 h", "20 h of shelf life left"])
def test_short_prose_quote_moves_to_next_shop_without_an_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    original = SmallPeers.handle

    async def handle(self: SmallPeers, request: httpx2.Request) -> httpx2.Response:
        response = await original(self, request)
        if (
            request.url.host == "first.test"
            and request.method == "POST"
            and b'"taskId"' not in request.content
        ):
            content = response.content.replace(
                b"X: asking 15000. Send your offer as a number.",
                f"This unit has {text}. Asking 15000.".encode(),
            )
            return httpx2.Response(
                200, content=content, headers={"content-type": "application/json"}
            )
        return response

    monkeypatch.setattr(SmallPeers, "handle", handle)
    outcome, peers = run_small(tmp_path, monkeypatch, "short_food")
    assert peers.visits == ["first.test", "second.test"]
    assert peers.offers == []
    assert outcome.shop == "http://second.test"
    assert outcome.receipt is not None
