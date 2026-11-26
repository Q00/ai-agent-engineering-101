"""11/17 08:07 (owner): market-first 25 SKUs and inextro's sofas at 1.05.

Holds follow market sales per hour behind a cash floor; the sofas pass at 1.05
and their units bought today accept down to cost x 1.05 (2 h test).
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from r3.endgame import ROUND_END
from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, Inventory, Unit
from r3.restock import (
    INEXTRO_SKUS,
    MARKET_FIRST_CASH_FLOOR,
    NEW_GOODS_STOP_COST,
    market_first_lines,
)
from r3.seller import INEXTRO_SINCE, Quote, inextro_quote
from tests.r3_stock import LAMP, unit

if TYPE_CHECKING:
    from pathlib import Path

NOW = ROUND_END - 16 * 3600
SOFA = "G24f5f979"


def ledger(tmp_path: Path, sales: dict[str, tuple[int, int]]) -> Path:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    rows: list[tuple[str, str, dict[str, object]]] = []
    for sku, (count, price) in sales.items():
        rows += [("settle", sku, {"amount": price})] * count
        rows.append(("supply", "", {"sku": sku, "qty": 1, "total": 100}))
    with sqlite3.connect(path) as db:
        for seq, (kind, sku, data) in enumerate(rows):
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, sku, amount, data)"
                " VALUES (?,?,?,?,?,?)",
                (seq, NOW - 60, kind, sku, data.get("amount"), json.dumps(data)),
            )
    return path


def goods(*skus: str) -> dict[str, CatalogItem]:
    return {
        s: CatalogItem(sku=s, category="sofa", kind="goods", list_price=200)
        for s in skus
    }


def test_hold_follows_market_sales_per_hour_and_largest_shortfall_goes_first(
    tmp_path: Path,
) -> None:
    # busy: 150 in 6 h = 25 an hour -> hold 25, 20 a run; slow: 24 -> hold 10.
    path = ledger(tmp_path, {"busy": (150, 120), "slow": (24, 130)})
    inv = Inventory(round=3, balance=10**9, units=())
    lines = market_first_lines(path, NOW, goods("busy", "slow"), inv)
    assert lines == [{"sku": "busy", "qty": 20}, {"sku": "slow", "qty": 10}]


def test_cash_floor_and_goods_cost_stop_cap_market_first(tmp_path: Path) -> None:
    path = ledger(tmp_path, {"busy": (150, 120)})
    poor = Inventory(round=3, balance=MARKET_FIRST_CASH_FLOOR + 550, units=())
    # 19:2x: under 150M cash only SKUs we sold 3+ of in 2 h are bought.
    assert market_first_lines(path, NOW, goods("busy"), poor) == []
    sold = {"busy": [NOW - 600] * 3}
    assert market_first_lines(path, NOW, goods("busy"), poor, sold) == [
        {"sku": "busy", "qty": 5}
    ]
    assert NEW_GOODS_STOP_COST == 1_150_000_000  # 1,000M 08:2x -> 1,150M 19:2x


def test_inextro_sofas_pass_at_105_and_hold_20(tmp_path: Path) -> None:
    assert SOFA in INEXTRO_SKUS
    # 106 paid on 100 cost: under 1.08, over 1.05.
    path = ledger(tmp_path, {SOFA: (24, 106), "other": (24, 106)})
    inv = Inventory(round=3, balance=10**9, units=())
    # 11/17 10:5x: every market-first SKU passes at 1.05 now; the sofa still holds 20.
    assert market_first_lines(path, NOW, goods(SOFA, "other"), inv) == [
        {"sku": SOFA, "qty": 20},
        {"sku": "other", "qty": 10},
    ]


def test_todays_sofa_units_accept_down_to_cost_x_105() -> None:
    lamp = unit(LAMP, 1, grade="A", cost=1_000_000, now=INEXTRO_SINCE + 7200)
    today = Unit.model_validate(lamp).model_copy(update={"sku": SOFA})
    priced = Quote(1_300_000, 1_100_000, 1_080_000, "A", clearance=False, stale=False)
    got = inextro_quote(today, priced)
    assert (got.ask, got.accept_at, got.floor) == (1_300_000, 1_050_000, 1_050_000)
    old = today.model_copy(update={"supplied_at": INEXTRO_SINCE - 60})
    assert inextro_quote(old, priced) is priced
    other = today.model_copy(update={"sku": LAMP})
    assert inextro_quote(other, priced) is priced


def test_goods_b_accept_floor_and_sofa_test_a_only() -> None:
    from r3.seller import b_goods_floor  # noqa: PLC0415 - local to this test

    lamp = unit(LAMP, 1, grade="B", cost=1_000_000, now=INEXTRO_SINCE + 7200)
    sofa_b = Unit.model_validate(lamp).model_copy(update={"sku": SOFA})
    low = Quote(1_300_000, 1_020_000, 1_020_000, "B", clearance=False, stale=False)
    # The sofa 1.05 line is for grade A only now.
    assert inextro_quote(sofa_b, low) is low
    # 500k+ ask: cost x 1.10; under 500k: cost x 1.06; C untouched.
    high = b_goods_floor(sofa_b, low)
    assert (high.ask, high.accept_at, high.floor) == (1_300_000, 1_100_000, 1_100_000)
    cheap = sofa_b.model_copy(update={"cost": 100_000})
    small = Quote(130_000, 102_000, 102_000, "B", clearance=False, stale=False)
    assert b_goods_floor(cheap, small).accept_at == 106_000
    c = Quote(1_300_000, 1_020_000, 1_020_000, "C", clearance=False, stale=False)
    assert b_goods_floor(sofa_b, c) is c
