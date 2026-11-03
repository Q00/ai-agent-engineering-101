"""Policy reviews use settlements, preserve uncertainty and feed every outer loop."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

from r3.advisor import Advisor
from r3.events import EventLedger
from r3.ledger_db import LedgerDB
from r3.manager import Manager
from r3.reflection import policy_reviews

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def change(events: EventLedger, version: int, stamp: float) -> None:
    events.record_policy_change(
        actor="test",
        layer="strategy",
        field="seller.markup_a",
        before=0.9,
        after=0.85,
        reason="review",
        expires_at=None,
        version=version,
        accepted=True,
    )
    with sqlite3.connect(events.path) as db:
        db.execute("UPDATE policy_changes SET ts=? WHERE version=?", (stamp, version))


def settled(  # noqa: PLR0913 - synthetic ledger row
    seq: int,
    stamp: float,
    shop: str,
    amount: int,
    *,
    buyer: str = "other",
    market_round: int = 3,
) -> dict[str, object]:
    return {
        "seq": seq,
        "ts": stamp,
        "kind": "settle",
        "round": market_round,
        "data": {"shop": shop, "buyer": buyer, "item": "sku", "amount": amount},
    }


def test_policy_review_compares_equal_r3_windows_without_claiming_causation(
    tmp_path: Path,
) -> None:
    now = time.time()
    at = now - 1800
    events, ledger = (
        EventLedger(tmp_path / "events.db"),
        LedgerDB(tmp_path / "ledger.db"),
    )
    change(events, 2, at)
    ledger.ingest(
        [
            settled(1, at - 100, "http://us", 100),
            settled(2, at - 100, "http://peer", 80, buyer="me"),
            settled(3, at + 100, "http://us/", 120),
            settled(4, at + 100, "http://us", 140),
            settled(5, at + 100, "http://peer", 90, buyer="me"),
            settled(6, at + 100, "http://us", 99999, market_round=2),
            {"seq": 7, "ts": now - 10, "kind": "order", "round": 3, "data": {}},
        ]
    )
    row = policy_reviews(events, ledger, our_shop="http://us", our_buyer="me", now=now)[
        0
    ]
    assert row["status"] == "descriptive_only"
    assert row["delta"] == {
        "shop_settles": 1,
        "shop_gross_revenue": 160,
        "buyer_settles": 0,
        "buyer_paid": 10,
    }
    assert row["causal_effect_established"] is False
    assert row["small_sample"] is True


def test_review_waits_for_a_complete_window_and_does_not_invent_zero(
    tmp_path: Path,
) -> None:
    now = time.time()
    events, ledger = (
        EventLedger(tmp_path / "events.db"),
        LedgerDB(tmp_path / "ledger.db"),
    )
    change(events, 2, now - 100)
    row = policy_reviews(events, ledger, our_shop="us", our_buyer="me", now=now)[0]
    assert row["status"] == "awaiting_complete_window"
    assert "delta" not in row


def test_round_boundary_and_overlapping_versions_are_explicit(tmp_path: Path) -> None:
    now = time.time()
    at = now - 1800
    events, ledger = (
        EventLedger(tmp_path / "events.db"),
        LedgerDB(tmp_path / "ledger.db"),
    )
    change(events, 2, at)
    ledger.ingest(
        [
            settled(1, at - 100, "us", 100, market_round=2),
            settled(2, at + 100, "us", 100),
        ]
    )
    row = policy_reviews(events, ledger, our_shop="us", our_buyer="me", now=now)[0]
    assert row["status"] == "round_boundary_or_missing_r3_data"
    assert "delta" not in row
    ledger.ingest(
        [
            settled(3, at - 100, "us", 100),
            {"seq": 4, "ts": now - 10, "kind": "order", "round": 3, "data": {}},
        ]
    )
    change(events, 3, at + 10)
    rows = policy_reviews(events, ledger, our_shop="us", our_buyer="me", now=now)
    assert all(row["status"] == "confounded" for row in rows)


def test_unavailable_ledger_is_unknown_not_zero(tmp_path: Path) -> None:
    now = time.time()
    events, ledger = (
        EventLedger(tmp_path / "events.db"),
        LedgerDB(tmp_path / "ledger.db"),
    )
    change(events, 2, now - 1800)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TABLE rows")
    assert (
        policy_reviews(events, ledger, our_shop="us", our_buyer="me")[0]["status"]
        == "unavailable"
    )


def test_stale_mirror_does_not_turn_a_partial_after_window_into_a_result(
    tmp_path: Path,
) -> None:
    now = time.time()
    at = now - 1800
    events = EventLedger(tmp_path / "events.db")
    ledger = LedgerDB(tmp_path / "ledger.db")
    change(events, 2, at)
    ledger.ingest(
        [
            settled(1, at - 100, "us", 100),
            settled(2, at + 100, "us", 100),
        ]
    )
    row = policy_reviews(events, ledger, our_shop="us", our_buyer="me", now=now)[0]
    assert row["status"] == "ledger_not_caught_up"
    assert "delta" not in row


def test_bounded_history_keeps_the_latest_changes_and_loop_runs(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.db")
    now = time.time()
    with sqlite3.connect(events.path) as db:
        db.executemany(
            "INSERT INTO policy_changes(ts,actor,layer,field,accepted) "
            "VALUES(?,?,?,?,?)",
            [(now - 250 + i, "test", "strategy", f"field-{i}", 1) for i in range(250)],
        )
        db.executemany(
            "INSERT INTO loop_runs(ts,role,outcome,detail) VALUES(?,?,?,?)",
            [(now - 520 + i, "scout", "held", str(i)) for i in range(520)],
        )
    changes = events.recent_policy_changes(1)
    assert changes[0]["field"] == "field-50"
    assert changes[-1]["field"] == "field-249"
    runs = events.loop_runs(now - 1000)
    assert runs[0]["detail"] == "20"
    assert runs[-1]["detail"] == "519"


def test_the_same_review_reaches_strategy_tactical_and_human_advisor_contexts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(
        tmp_path, "https://market.test", shop_url="us", buyer_name="me", shop_name="me"
    )
    monkeypatch.setattr(manager, "cached_json", lambda *_a, **_k: None)
    change(manager.events, 2, time.time() - 1800)
    (tmp_path / "r3-reviews.json").write_text(
        json.dumps({"our_reviews": 3, "model_digest_valid": True})
    )
    try:
        strategy = manager.summary()["policy_reviews"]
        assert strategy[0]["version"] == 2
        assert manager.tactical_summary()["policy_reviews"] == strategy
        assert Advisor(manager).context()["policy_reviews"] == strategy
        assert (
            manager.tactical_summary()["review_digest"]
            == manager.summary()["review_digest"]
        )
        assert "leaders_now" in manager.tactical_summary()
    finally:
        manager.http.close()
