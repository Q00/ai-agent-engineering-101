"""Food C held beyond its own C sales cuts rebuying; breadth obeys the new-SKU stop."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from r3.endgame import ROUND_END
from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, DemandRow, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import (
    NEW_SKU_STOP_UNITS,
    DemandSignal,
    RestockState,
    c_sales,
    plan,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = ROUND_END - 60 * 3600
POLICY = RestockPolicy(
    growth_enabled=False,
    units_per_sku=20,
    max_units=3000,
    depth_skus=1,
    seed_budget=0,
    reserve_cash=0,
    stock_share=0.8,
    goods_min_list_price=0,
    goods_max_list_price=5_000_000,
    food_share=1,
    min_food_shelf_hours=168,
)
FOOD = CatalogItem(
    sku="food", category="food", kind="food", list_price=10000, shelf_hours=168
)
C_FOOD = tuple(
    Unit(
        unit=f"c{i}",
        sku="food",
        category="food",
        kind="food",
        list_price=10000,
        cost=5000,
        grade="C",
        supplied_at=NOW - 3600,
        expires_at=NOW + 24 * 3600,
    )
    for i in range(50)
)
ECON = {"food": (1000.0, 12), **{f"other{i}": (10000.0, 50) for i in range(4)}}
SOLD = RestockState(last_sold_at={"food": NOW - 100}, sold_log={"food": [NOW - 100]})


def food_plan(c_sold: dict[str, int] | None, raters: tuple[str, ...] = ()) -> list:
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=C_FOOD),
        {"food": FOOD},
        (),
        POLICY,
        SOLD,
        NOW,
        DemandSignal({}, {}),
        ECON,
        rater_skus=raters,
        c_sold=c_sold,
    )
    return lines


def test_unsold_food_c_cuts_rebuying_to_the_a_b_floor() -> None:
    # Given: 50 C units and no A/B of a food that sold 12 in 12 h (Codex repro: 6
    # more were planned at FOOD_SALES_SHARE 0.5, 12 at 1.0). When none of the C
    # sold, only the A/B floor of 2 is bought.
    assert food_plan(None) == [{"sku": "food", "qty": 12}]  # FOOD_SALES_SHARE 1.0
    assert food_plan({}) == [{"sku": "food", "qty": 2}]
    assert food_plan({}, ("food",)) == [{"sku": "food", "qty": 2}]


def test_food_c_that_sells_does_not_cut_rebuying() -> None:
    # When the C units sold as fast as they are held, the target stands.
    assert food_plan({"food": 50}) == [{"sku": "food", "qty": 12}]


def test_breadth_obeys_the_new_sku_stop() -> None:
    # Given: NEW_SKU_STOP_UNITS units held and demand for a food we hold none of.
    filler = tuple(
        Unit(
            unit=f"x{i}",
            sku="filler",
            category="x",
            kind="goods",
            list_price=1000,
            cost=100,
            grade="A",
            supplied_at=NOW - 3600,
        )
        for i in range(NEW_SKU_STOP_UNITS)
    )
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=filler),
        {"food": FOOD},
        (DemandRow(category="food", kind="food"),),
        POLICY,
        RestockState(),
        NOW,
        DemandSignal({"food": 100}, {"food": 100}),
        {},
    )
    # Then: no new SKU is started (Codex repro planned 2 units of it).
    assert lines == []


def test_c_sales_count_our_settles_not_receipts(tmp_path: Path) -> None:
    # Given: our ledger with C settles, an A settle and another shop's C settle.
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    ours = "https://ours.test"
    rows = [
        (1, NOW - 600, "settle", ours + "/", "food", "C"),
        (2, NOW - 500, "settle", ours, "food", "C"),
        (3, NOW - 400, "settle", ours, "food", "A"),
        (4, NOW - 300, "settle", "https://other.test", "food", "C"),
        (5, NOW - 13 * 3600, "settle", ours, "food", "C"),  # outside 12 h
    ]
    with closing(sqlite3.connect(path)) as db, db:
        for seq, ts, kind, shop, sku, grade in rows:
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, shop, sku, grade, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (seq, ts, kind, shop, sku, grade, json.dumps({"grade": grade})),
            )
    # Then: two of our C settles in 12 h; no shop URL means the check stays off.
    assert c_sales(path, ours, NOW) == {"food": 2}
    assert c_sales(path, "", NOW) is None
