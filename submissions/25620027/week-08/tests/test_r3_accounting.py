"""Week09 Fig08 examples and incomplete-source financial boundaries."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import TYPE_CHECKING

import anyio
import pytest

from r3.accounting import DAY, STOCK_FILE, StockJournal, financial_window
from r3.events import EventLedger
from r3.ledger_db import LedgerDB
from r3.recorder import record_loop
from r3.reflection import policy_reviews
from tests.test_r3_reflection import change

if TYPE_CHECKING:
    from pathlib import Path

SHOP = "https://our-shop"


def unit(
    name: str,
    supplied: float,
    *,
    cost: int = 50_000,
    kind: str = "goods",
    expires: float | None = None,
) -> dict[str, object]:
    return {
        "unit": name,
        "sku": "sku",
        "kind": kind,
        "cost": cost,
        "supplied_at": supplied,
        "expires_at": expires,
        "list_price": 100_000,
    }


def snapshot(units: list[dict[str, object]], balance: int) -> dict[str, object]:
    return {
        "round": 3,
        "units": units,
        "balance": balance,
        "start": 5_000_000,
        "injected": 0,
        "storage_per_day": 0.01,
    }


def event(seq: int, stamp: float, kind: str, **data: object) -> dict[str, object]:
    return {
        "seq": seq,
        "ts": stamp,
        "kind": kind,
        "round": 3,
        "data": {"shop": SHOP, **data},
    }


def window(
    stock: StockJournal, ledger: LedgerDB, start: float, end: float
) -> dict[str, object]:
    with sqlite3.connect(ledger.path) as db:
        return financial_window(
            stock.path.resolve(), db, start=start, end=end, shop=SHOP
        )


def cover(stock: StockJournal, start: float, end: float) -> None:
    """Fill observation timestamps; costs still come from the inventory fixtures."""
    with sqlite3.connect(stock.path) as db:
        previous = db.execute("SELECT * FROM snapshots ORDER BY ts LIMIT 1").fetchone()
        db.executemany(
            "INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?)",
            [
                (start + i * 30, *previous[1:])
                for i in range(int((end - start) / 30) + 1)
                if start + i * 30 < end
            ],
        )


@pytest.mark.parametrize(("broken", "expected"), [(False, 15_000), (True, -55_000)])
def test_week09_goods_example_includes_ten_days_storage_and_full_warranty_refund(
    tmp_path: Path,
    broken: bool,  # noqa: FBT001 - parametrized lecture outcomes
    expected: int,
) -> None:
    # Exact Fig08: list100k, cost50k, ten days storage5k, price70k.
    start, end = 1000.0, 1000.0 + 10 * DAY + 1
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([unit("g", start)], 4_950_000), stamp=start)
    rows = [event(1, end - 1, "settle", unit="g", amount=70_000, warranty_hours=6)]
    if broken:
        rows.append(event(2, end - 0.5, "broken", unit="g", refund=70_000))
    ledger.ingest(rows)
    storage = 5000
    assert stock.observe(
        snapshot([], round(4_950_000 + 70_000 - storage - (70_000 if broken else 0))),
        stamp=end,
    )
    # Replay a ten-day lecture fixture; production comparisons use 15 min and
    # intentionally retain only two days of snapshots, not ten days of raw data.
    with sqlite3.connect(stock.path) as db:
        db.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?,?,?)",
            (start, 4_950_000, 5_000_000, 0, 50_000, 0.01),
        )
    cover(stock, start, end)
    result = window(stock, ledger, start, end)
    assert result["financial_status"] == "complete"
    assert result["shop_cost_of_goods_sold"] == 50_000
    assert result["shop_storage_cost"] == 5000
    assert result["shop_refunds"] == (70_000 if broken else 0)
    assert result["shop_net_profit"] == expected


def test_week09_expired_food_example_is_minus_13000(tmp_path: Path) -> None:
    start, end = 1000.0, 1100.0
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(
        snapshot([unit("f", start, cost=13_000, kind="food", expires=1050)], 4_987_000),
        stamp=start,
    )
    assert stock.observe(snapshot([], 4_987_000), stamp=end)
    cover(stock, start, end)
    result = window(stock, ledger, start, end)
    assert result["financial_status"] == "complete"
    assert result["shop_expired_food_cost"] == 13_000
    assert result["shop_net_profit"] == -13_000
    assert result["shop_storage_cost"] == 0


def test_restock_moves_cash_to_assets_and_is_not_subtracted_from_profit(
    tmp_path: Path,
) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([], 5_000_000), stamp=1000)
    assert stock.observe(
        snapshot([unit("f", 1050, cost=13_000, kind="food", expires=5000)], 4_987_000),
        stamp=1100,
    )
    cover(stock, 1000, 1100)
    result = window(stock, ledger, 1000, 1100)
    assert result["shop_restock_spend"] == 13_000
    assert result["shop_cash_change"] == -13_000
    assert result["shop_inventory_asset_change"] == 13_000
    assert result["shop_cost_of_goods_sold"] == 0
    assert result["shop_net_profit"] == 0
    assert result["official_cash_profit_at_end"] == -13_000


def test_old_sale_refund_hits_only_refund_period_and_other_shops_are_excluded(
    tmp_path: Path,
) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([], 5_020_000), stamp=1000)
    assert stock.observe(snapshot([], 4_950_000), stamp=1100)
    cover(stock, 1000, 1100)
    ledger.ingest(
        [
            event(1, 900, "settle", unit="g", amount=70_000, warranty_hours=6),
            event(2, 1050, "broken", unit="g", refunded=True),
            event(3, 1060, "broken", unit="other", shop="https://peer", refund=90_000),
        ]
    )
    result = window(stock, ledger, 1000, 1100)
    assert result["shop_refunds"] == 70_000
    assert result["shop_cost_of_goods_sold"] == 0
    assert result["shop_net_profit"] == -70_000


@pytest.mark.parametrize(
    ("refund", "expected"), [(False, 0), (0, 0), (True, 70_000), (70_000, 70_000)]
)
def test_break_is_not_automatically_a_refund(
    tmp_path: Path, refund: object, expected: int
) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([], 5_020_000), stamp=1000)
    assert stock.observe(snapshot([], 5_020_000 - expected), stamp=1100)
    cover(stock, 1000, 1100)
    ledger.ingest(
        [
            event(1, 900, "settle", unit="g", amount=70_000, warranty_hours=0),
            event(2, 1050, "break", unit="g", refund=refund),
        ]
    )
    assert window(stock, ledger, 1000, 1100)["shop_net_profit"] == -expected


def test_missing_cost_refund_or_snapshot_never_invents_profit(tmp_path: Path) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([], 5_000_000), stamp=1000)
    assert stock.observe(snapshot([], 5_070_000), stamp=1100)
    cover(stock, 1000, 1100)
    ledger.ingest(
        [
            event(1, 1050, "settle", unit="unseen", amount=70_000),
            event(2, 1060, "broken", unit="unknown"),
        ]
    )
    result = window(stock, ledger, 1000, 1100)
    assert result["shop_net_profit"] is None
    assert "sold_unit_cost_missing" in result["financial_gaps"]
    assert "refund_amount_or_warranty_unknown" in result["financial_gaps"]
    assert window(stock, ledger, 1000, 1200)["shop_net_profit"] is None


def test_unknown_removal_or_bankruptcy_cannot_become_fake_profit(
    tmp_path: Path,
) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([unit("g", 1000)], 4_950_000), stamp=1000)
    last = snapshot([], 5_000_000)
    last["injected"] = 50_000
    assert stock.observe(last, stamp=1100)
    cover(stock, 1000, 1100)
    result = window(stock, ledger, 1000, 1100)
    assert result["shop_net_profit"] is None
    assert "unexplained_inventory_removal" in result["financial_gaps"]
    assert "bankruptcy_or_capital_injection" in result["financial_gaps"]


def test_bad_stale_or_repeated_inventory_is_not_an_empty_stock_snapshot(
    tmp_path: Path,
) -> None:
    stock = StockJournal(tmp_path / STOCK_FILE)
    source = tmp_path / "inventory.json"
    source.write_text(json.dumps(snapshot([], 5_000_000)))
    os.utime(source, (1000, 1000))
    assert stock.capture_file(source)
    assert not stock.capture_file(source)
    source.write_text("{}")
    os.utime(source, (1001, 1001))
    assert not stock.capture_file(source)
    source.write_text("{")
    os.utime(source, (1002, 1002))
    assert not stock.capture_file(source)
    assert not stock.observe(snapshot([], 5_000_000), stamp=999)
    with sqlite3.connect(stock.path) as db:
        assert db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1


def test_more_revenue_with_less_profit_is_worsened_and_a_fold_merges_with_its_move(
    tmp_path: Path,
) -> None:
    at = 2000.0
    now = 3000.0
    events, ledger = (
        EventLedger(tmp_path / "events.db"),
        LedgerDB(tmp_path / "ledger.db"),
    )
    stock = StockJournal(tmp_path / STOCK_FILE)
    before = [
        unit(f"b{i}", 1000, cost=100, kind="food", expires=5000) for i in range(5)
    ]
    after = [unit(f"a{i}", 1000, cost=300, kind="food", expires=5000) for i in range(5)]
    assert stock.observe(snapshot(before + after, 4_998_000), stamp=1100)
    assert stock.observe(snapshot(after, 4_999_000), stamp=2000)
    assert stock.observe(snapshot([], 5_000_250), stamp=2900)
    cover(stock, 1100, 2900)
    rows = [event(i + 1, 1500, "settle", unit=f"b{i}", amount=200) for i in range(5)]
    rows += [event(i + 6, 2500, "settle", unit=f"a{i}", amount=250) for i in range(5)]
    rows += [event(11, 2990, "order")]
    ledger.ingest(rows)
    change(events, 2, at)
    row = policy_reviews(events, ledger, our_shop=SHOP, our_buyer="me", now=now)[0]
    assert row["delta"]["shop_gross_revenue"] == 250
    assert row["delta"]["shop_net_profit"] == -750
    assert row["assessment"] == "worsened"
    assert row["causal_effect_established"] is False
    # And: a second version a second later is the same change (merged), not a
    # confound that hides the result.
    change(events, 3, at + 1)
    row = policy_reviews(events, ledger, our_shop=SHOP, our_buyer="me", now=now)[-1]
    assert row["merged_versions"] == [2, 3]
    assert row["assessment"] == "worsened"


def test_higher_profit_with_less_revenue_is_improved(tmp_path: Path) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    events = EventLedger(tmp_path / "events.db")
    before = [
        unit(f"b{i}", 1000, cost=100, kind="food", expires=5000) for i in range(5)
    ]
    after = [unit(f"a{i}", 1000, cost=25, kind="food", expires=5000) for i in range(5)]
    stock.observe(snapshot(before + after, 4_999_375), stamp=1100)
    stock.observe(snapshot(after, 5_000_375), stamp=2000)
    stock.observe(snapshot([], 5_001_125), stamp=2900)
    cover(stock, 1100, 2900)
    ledger.ingest(
        [event(i + 1, 1500, "settle", unit=f"b{i}", amount=200) for i in range(5)]
        + [event(i + 6, 2500, "settle", unit=f"a{i}", amount=150) for i in range(5)]
        + [event(11, 2990, "order")]
    )
    change(events, 2, 2000)
    row = policy_reviews(events, ledger, our_shop=SHOP, our_buyer="me", now=3000)[0]
    assert row["delta"]["shop_gross_revenue"] == -250
    assert row["delta"]["shop_net_profit"] == 125
    assert row["assessment"] == "improved"


def test_missing_observations_and_duplicate_breaks_block_an_improvement(
    tmp_path: Path,
) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    stock.observe(snapshot([], 5_000_000), stamp=1000)
    stock.observe(snapshot([], 4_860_000), stamp=1100)
    ledger.ingest(
        [
            event(1, 900, "settle", unit="g", amount=70_000, warranty_hours=6),
            event(2, 1050, "broken", unit="g", refund=True),
            event(3, 1060, "broken", unit="g", refund=True),
        ]
    )
    result = window(stock, ledger, 1000, 1100)
    assert result["shop_refunds"] == 70_000
    assert "duplicate_unit_break" in result["financial_gaps"]
    assert "inventory_observation_gap" in result["financial_gaps"]
    assert result["shop_net_profit"] is None


def test_sold_food_is_not_also_counted_as_expired(tmp_path: Path) -> None:
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    stock.observe(
        snapshot([unit("f", 1000, cost=13_000, kind="food", expires=1070)], 4_987_000),
        stamp=1000,
    )
    stock.observe(snapshot([], 5_007_000), stamp=1100)
    cover(stock, 1000, 1100)
    ledger.ingest([event(1, 1050, "settle", unit="f", amount=20_000)])
    result = window(stock, ledger, 1000, 1100)
    assert result["shop_expired_food_cost"] == 0
    assert result["shop_net_profit"] == 7000


def test_existing_recorder_captures_costs_off_the_trade_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AX_META_FEEDBACK", raising=False)
    monkeypatch.delenv("AX_META_PROFILE", raising=False)
    md = tmp_path / ".market"
    md.mkdir()
    (md / "inventory.json").write_text(
        json.dumps(snapshot([unit("g", 1000)], 4_950_000))
    )

    async def run() -> None:
        with anyio.move_on_after(0.2):
            await record_loop(
                tmp_path / "memory.sqlite", tmp_path / "buyer-memory.json"
            )

    anyio.run(run)
    with sqlite3.connect(tmp_path / STOCK_FILE) as db:
        assert db.execute("SELECT cost FROM stock WHERE unit=?", ("g",)).fetchone() == (
            50_000,
        )


def test_storage_not_yet_charged_by_the_market_is_not_a_reconciliation_gap(
    tmp_path: Path,
) -> None:
    # Given: the lecture goods unit held ten days, sold, but the market has not
    # taken the 5,000 storage from the balance yet (first-night live reviews).
    start, end = 1000.0, 1000.0 + 10 * DAY + 1
    stock, ledger = (
        StockJournal(tmp_path / STOCK_FILE),
        LedgerDB(tmp_path / "ledger.db"),
    )
    assert stock.observe(snapshot([unit("g", start)], 4_950_000), stamp=start)
    ledger.ingest([event(1, end - 1, "settle", unit="g", amount=70_000)])
    assert stock.observe(snapshot([], 4_950_000 + 70_000), stamp=end)
    with sqlite3.connect(stock.path) as db:
        db.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?,?,?)",
            (start, 4_950_000, 5_000_000, 0, 50_000, 0.01),
        )
    cover(stock, start, end)
    result = window(stock, ledger, start, end)
    # Then: the residual equals the accrued storage, is named, and profit stands.
    assert result["financial_status"] == "complete"
    assert result["storage_not_yet_charged"] == 5000
    assert result["shop_net_profit"] == 70_000 - 50_000 - 5000
