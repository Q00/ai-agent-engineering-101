"""Current-order affordability must not inherit an older order's budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.memory import Memory, Trade
from market_a2a.models import Purchase
from tests.market_fakes import ScriptedHarness, ShopTransport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "history",
    [
        ("bookshelf", 306, True, False),
        ("bookshelf", 260, True, True),
        ("chair", 210, False, None),
    ],
)
def test_current_budget_when_history_has_another_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: tuple[str, int, bool, bool | None],
) -> None:
    # Given: a historical price was valid for its own higher budget.
    old_item, old_price, same_item, affordable = history
    peer = ShopTransport()

    def create_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle))

    monkeypatch.setattr("market_a2a.buyer.create_client", create_client)
    registry = tmp_path / "shops.json"
    _ = registry.write_text(
        '[{"name":"South","url":"http://south.test","reviews":[]}]',
        encoding="utf-8",
    )
    memory_path = tmp_path / "memory.json"
    Memory().append(
        "South",
        Trade(
            item=old_item,
            budget=360,
            selection_reason="Recorded earlier order",
            purchase=Purchase(
                completed=True,
                item=old_item,
                price=old_price,
                receipt={"price": old_price},
            ),
        ),
    ).save(memory_path)
    harness = ScriptedHarness(
        [
            ("select_shop", {"index": 0, "reason": "Inspect current order"}),
            ("walk_away", {"reason": "No purchase requested by this fixture"}),
            ("leave_review", {"stars": 3, "review": "No deal"}),
        ]
    )
    logs: list[str] = []
    # When: selection receives a bookshelf order whose budget is only 270.
    _ = anyio.run(
        market_buy,
        harness,
        MarketRequest(registry, "bookshelf", 270, memory_path),
        logs.append,
    )
    # Then: the machine input distinguishes current affordability and item identity.
    mapping = TypeAdapter(dict[str, JsonValue])
    sequence = TypeAdapter(list[dict[str, JsonValue]])
    state = mapping.validate_python(harness.states[0])
    shop = sequence.validate_python(state["shops"])[0]
    facts = sequence.validate_python(shop["own_trades"])[0]
    assert facts["same_requested_item"] is same_item
    assert facts["price_within_current_budget"] is affordable
    assert facts["budget"] == 360
    assert "selection_reason" not in facts
