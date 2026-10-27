"""Keep compact priors separate from original retrievable trade observations."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest

from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.market_decision import Decision, MarketShop
from market_a2a.memory import Memory, Trade
from market_a2a.memory_selection import MemorySelection
from market_a2a.models import Purchase
from tests.market_fakes import ScriptedHarness, ShopTransport
from tests.r3_ledger import JSON

if TYPE_CHECKING:
    from pathlib import Path


def observation(task: str) -> Trade:
    return Trade(
        item="lamp",
        budget=120,
        selection_reason="Recorded observation",
        purchase=Purchase(
            completed=True,
            item="lamp",
            shop="http://old.test",
            price=90,
            receipt={"task": task, "item": "lamp", "price": 90},
        ),
    )


def test_prior_stays_bounded_while_old_evidence_remains_addressable() -> None:
    # Given:20 and2000 records for the same stable shop name.
    first = observation("early-original-task")
    shops = (MarketShop(name="Stable", url="http://new.test"),)
    small = MemorySelection(Decision(shops), Memory(shops={"Stable": (first,) * 20}))
    large = MemorySelection(Decision(shops), Memory(shops={"Stable": (first,) * 2000}))
    # When: compact both and retrieve the original event by ordinal rather than search.
    before = small.prior(0, "lamp")
    after = large.prior(0, "lamp")
    found = anyio.run(
        large.dispatch, "lookup_trade_evidence", '{"shop_index":0,"record_index":0}'
    )
    # Then: summary growth is bounded and retrieval never certifies payment.
    assert after.records == 2000
    assert len(after.recent_evidence) == 3
    assert len(after.model_dump_json()) - len(before.model_dump_json()) < 128
    assert not found.terminal
    assert large.decision.selection is None
    assert found.data == {
        "shop_name": "Stable",
        "record_index": 0,
        "evidence_sha256": hashlib.sha256(first.model_dump_json().encode()).hexdigest(),
        "source": "own_saved_observation",
        "record": json.loads(first.model_dump_json()),
        "settlement_evidence": None,
        "proof_status": "receipt_observed_not_a_verified_settlement",
    }
    print(
        json.dumps(
            {
                "records": 2000,
                "prior_bytes": len(after.model_dump_json().encode()),
                "full_memory_bytes": len(large.memory.model_dump_json().encode()),
            }
        )
    )


@pytest.mark.parametrize(
    "arguments",
    [
        '{"shop_index":1,"record_index":0}',
        '{"shop_index":0,"record_index":9}',
        '{"shop_index":0,"record_index":0,"path":"/etc/passwd"}',
    ],
)
def test_evidence_tool_cannot_escape_its_snapshot(arguments: str) -> None:
    # Given: one registry shop with one own-trade record.
    decision = Decision((MarketShop(name="Stable", url="http://new.test"),))
    tools = MemorySelection(decision, Memory(shops={"Stable": (observation("old"),)}))
    # When: request another shop, missing record, or an arbitrary file path.
    result = anyio.run(tools.dispatch, "lookup_trade_evidence", arguments)
    # Then: the model receives feedback without selecting or exposing another source.
    assert not result.terminal
    assert decision.selection is None
    assert "error" in JSON.validate_python(result.data)


def test_market_buyer_can_lookup_evidence_before_authorizing_a_shop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: saved experience under a stable name and a changed current URL.
    peer = ShopTransport()
    monkeypatch.setattr(
        "market_a2a.buyer.create_client",
        lambda: httpx2.AsyncClient(
            transport=httpx2.MockTransport(peer.handle),
        ),
    )
    registry, memory_path = tmp_path / "shops.json", tmp_path / "memory.json"
    _ = registry.write_text(
        json.dumps([{"name": "Stable", "url": "http://south.test", "reviews": []}])
    )
    Memory(shops={"Stable": (observation("prior-task"),)}).save(memory_path)
    harness = ScriptedHarness(
        [
            ("lookup_trade_evidence", {"shop_index": 0, "record_index": 0}),
            (
                "select_shop",
                {"index": 0, "reason": "Inspect own observation before choosing"},
            ),
            ("offer", {"price": 120, "reason": "Within budget"}),
            ("leave_review", {"stars": 3, "review": "Receipt received."}),
        ]
    )
    # When: use the real market buyer and the new retrieval dispatcher.
    result = anyio.run(
        market_buy, harness, MarketRequest(registry, "lamp", 120, memory_path), print
    )
    # Then: retrieval itself did not authorize a request; normal guarded trading did.
    assert result.receipt == peer.receipt
    assert harness.feedback[0].terminal is False
    assert harness.feedback[1].terminal is True
    assert len(Memory.load(memory_path).shops["Stable"]) == 2
    assert {request.url.host for request in peer.requests} == {"south.test"}
