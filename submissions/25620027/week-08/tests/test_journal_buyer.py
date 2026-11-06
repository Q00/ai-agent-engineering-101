"""The actual market buyer uses journal memory without waiting for export."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2

from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.memory_journal import MemoryJournal
from tests.market_fakes import ScriptedHarness, ShopTransport
from tests.test_memory_selection import observation

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_buyer_reads_and_updates_journal_before_any_context_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one own observation committed by another process but no JSON export.
    database, snapshot = tmp_path / "memory.sqlite", tmp_path / "memory.json"
    journal = MemoryJournal(database)
    journal.record("Stable", observation("original-evidence"))
    monkeypatch.setenv("AX_MEMORY_JOURNAL", str(database))
    peer = ShopTransport()
    monkeypatch.setattr(
        "market_a2a.buyer.create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    registry = tmp_path / "shops.json"
    _ = registry.write_text(
        json.dumps([{"name": "Stable", "url": "http://south.test", "reviews": []}])
    )
    harness = ScriptedHarness(
        [
            ("lookup_trade_evidence", {"shop_index": 0, "record_index": 0}),
            ("select_shop", {"index": 0, "reason": "Use committed own evidence"}),
            ("offer", {"price": 120, "reason": "Authorized proposal"}),
            ("leave_review", {"stars": 3, "review": "Observed receipt at120."}),
        ]
    )
    # When: use the real selection, negotiation, recording and review paths.
    result = anyio.run(
        market_buy, harness, MarketRequest(registry, "lamp", 120, snapshot), print
    )
    # Then: the raw receipt survives and one trade, not two writes, is added.
    assert result.receipt == peer.receipt
    state = journal.snapshot()[1]
    assert state.profiles["Stable"].observations == 2
    assert state.shops["Stable"][-1].evaluation is not None
    assert not snapshot.exists()
