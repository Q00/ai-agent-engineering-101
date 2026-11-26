from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from r3.buyer_report import write_buyer_report
from r3.buyer_report_models import MATURITY, BuyerReport, ReportPayload, budget_band
from r3.events import EventLedger
from r3.market_files import Order, PaymentLine

NOW = 100 * 3600 + 300


def setup(tmp_path: Path) -> tuple[EventLedger, Path, Path, Path]:
    events = EventLedger(tmp_path / "events.sqlite")
    ledger = tmp_path / "ledger.sqlite"
    with closing(sqlite3.connect(ledger)) as db, db:
        _ = db.execute(
            """CREATE TABLE rows(seq INTEGER PRIMARY KEY,ts REAL,kind TEXT,buyer
TEXT,round INTEGER,data TEXT)"""
        )
    market = tmp_path / ".market"
    market.mkdir()
    _ = (market / "catalog.json").write_text(
        '{"sku":{"sku":"sku","kind":"goods","list_price":300000}}'
    )
    return events, ledger, market, tmp_path / "r3-buyer-report.json"


def append(
    ledger: Path, seq: int, ts: float, kind: str, payload: ReportPayload
) -> None:
    with closing(sqlite3.connect(ledger)) as db, db:
        _ = db.execute(
            "INSERT INTO rows VALUES (?,?,?,?,3,?)",
            (seq, ts, kind, "me", payload.model_dump_json()),
        )


def report(
    paths: tuple[EventLedger, Path, Path, Path], now: float = NOW
) -> BuyerReport:
    events, ledger, market, output = paths
    return write_buyer_report(events.path, ledger, market, "me", output, now=now)


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (None, "unknown"),
        (0, "unknown"),
        (49999, "<50000"),
        (50000, "50000-199999"),
        (199999, "50000-199999"),
        (200000, "200000-499999"),
        (500000, "500000-999999"),
        (1000000, ">=1000000"),
    ],
)
def test_budget_boundaries(budget: int | None, expected: str) -> None:
    assert budget_band(budget) == expected


def test_metadata_before_attempt_and_authoritative_cohort(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, output = paths
    order = Order(order="o", budget=200000, kind="goods", expires=NOW - 500)
    events.record_order_metadata(order, observed_at=NOW)
    events.record_order_metadata(order, observed_at=NOW + 100)
    append(ledger, 1, NOW - 1800, "order", ReportPayload(order="o"))
    append(ledger, 2, NOW, "tick", ReportPayload())
    actual = report(paths)
    assert actual.bands["200000-499999"].orders == 1
    assert actual.authoritative_order_times == 1
    assert actual.local_observation_times == 0
    assert actual.bands["200000-499999"].mature_surplus is None
    assert output.exists()
    assert len(output.with_suffix(".jsonl").read_text().splitlines()) == 1


def test_retry_actual_settlement_and_unknown_budget(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, _ = paths
    events.record_order_metadata(
        Order(order="o", budget=200000), observed_at=NOW - 1800
    )
    for reason in ("shop timeout", ""):
        events.record_attempt(
            order="o",
            shop="https://shop/",
            shop_name="shop",
            sku="sku",
            list_price=300000,
            price=200000,
            outcome="no_deal",
            reason=reason,
        )
    append(ledger, 1, NOW - 1800, "order", ReportPayload(order="historical"))
    append(
        ledger,
        2,
        NOW - 1500,
        "settle",
        ReportPayload(order="o", unit="u", item="sku", amount=200000),
    )
    append(ledger, 3, NOW, "tick", ReportPayload())
    first = report(paths)
    second = report(paths)
    metric = second.bands["200000-499999"]
    assert metric == first.bands["200000-499999"]
    assert (
        metric.settled,
        metric.retries,
        metric.retry_settled,
        metric.shop_timeouts,
    ) == (1, 1, 1, 1)
    assert metric.provisional_surplus == 100000
    assert metric.mature_surplus is None
    assert second.bands["unknown"].orders == 1
    assert events.shop_timeout_counts() == {"https://shop": 1}


def test_mature_unit_refund_and_stale_null(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, _ = paths
    issued = (NOW - MATURITY) // 3600 * 3600 - 1800
    events.record_order_metadata(
        Order(order="o", budget=200000, kind="goods"), observed_at=issued
    )
    append(
        ledger,
        1,
        issued + 5,
        "settle",
        ReportPayload(order="o", unit="u", item="sku", amount=200000),
    )
    append(ledger, 2, issued + 600, "broken", ReportPayload(unit="u", refunded=100000))
    append(ledger, 3, NOW, "tick", ReportPayload())
    actual = report(paths)
    assert actual.mature_bands["200000-499999"].mature_surplus == -100000
    with closing(sqlite3.connect(ledger)) as db, db:
        _ = db.execute("UPDATE rows SET ts=? WHERE seq=3", (NOW - 181,))
    stale = report(paths)
    assert not stale.ledger_fresh
    assert stale.mature_bands["200000-499999"].mature_surplus is None


def test_failed_zero_pending_null_and_no_stock(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, _ = paths
    events.record_order_metadata(
        Order(order="fail", budget=100, expires=NOW - 500), observed_at=NOW - 1800
    )
    events.record_order_metadata(
        Order(order="pending", budget=200000), observed_at=NOW - 1800
    )
    _ = events.sync_payments([PaymentLine(order="fail", paid=False)])
    events.record_attempt(
        order="fail",
        shop="",
        shop_name="",
        sku="",
        list_price=0,
        price=0,
        outcome="no_deal",
        reason="out of stock",
    )
    append(ledger, 1, NOW, "tick", ReportPayload())
    actual = report(paths)
    assert actual.bands["<50000"].mature_surplus_per_order == 0
    assert actual.bands["<50000"].no_stock_rate == 1
    assert actual.bands["200000-499999"].mature_surplus_per_order is None


def test_missing_source_raises_without_success_output(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    paths[1].unlink()
    with pytest.raises(sqlite3.OperationalError):
        _ = report(paths)
    assert not paths[3].exists()
