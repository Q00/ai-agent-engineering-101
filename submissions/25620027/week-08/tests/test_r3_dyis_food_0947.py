"""11/17 09:47 (owner): dyishappy's B/C food SKUs lead, fresh holds replace 200.

SKUs buyer-02 or dyishappy bought lately skip FRESH_MAX_HELD and keep at most 40
fresh units; the SKUs dyishappy got B or C for get up to three food lines first.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, Inventory
from r3.policy import RestockPolicy
from r3.restock import (
    DYIS_STOP_UNITS,
    DemandSignal,
    RestockState,
    dyishappy_food_lines,
    plan,
    rater_food,
)
from tests.test_r3_fresh_list import NOW, SHELF, SKU, food

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from r3.market_files import Unit

SHOP = "https://ax.test"
ITEM = CatalogItem(
    sku=SKU, category="식품>생수", kind="food", list_price=5400, shelf_hours=SHELF
)


def test_rater_food_lists_recent_skus_and_dyishappy_bc_counts(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    other = CatalogItem(sku="Gother", category="식품", kind="food", list_price=9000)
    rows = [
        ("dyishappy", SKU, "B", NOW - 600),
        ("dyishappy", SKU, "C", NOW - 900),
        ("dyishappy", "Gother", "A", NOW - 900),
        ("buyer-02", "Gother", "B", NOW - 3600),
        ("dyishappy", "Gother", "B", NOW - 3 * 3600 + 60),  # past 2 h: open only
        ("usagi", "Gnone", "B", NOW - 60),
    ]
    with sqlite3.connect(path) as db:
        for seq, (buyer, sku, grade, ts) in enumerate(rows):
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, buyer, shop, sku, grade, data)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (seq, ts, "settle", buyer, SHOP + "/", sku, grade, json.dumps({})),
            )
    opened, short = rater_food(path, SHOP, NOW, {SKU: ITEM, "Gother": other})
    assert opened == frozenset({SKU, "Gother"})
    assert short == [(2, SKU)]


def test_dyishappy_lines_fill_fresh_units_up_to_forty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from r3 import restock  # noqa: PLC0415 - local to this test

    # Off since the 12:14 judgement; the lines still work when put back.
    monkeypatch.setattr(restock, "DYIS_LINES_ON", True)
    stale = tuple(food(f"s{i}", 0.3 * SHELF) for i in range(250))
    fresh = tuple(food(f"f{i}", 0.9 * SHELF) for i in range(30))
    inv = Inventory(round=3, balance=10**9, units=stale + fresh)
    got = dyishappy_food_lines([(5, SKU)], {SKU: ITEM}, inv, NOW, set())
    assert got == [{"sku": SKU, "qty": 20}]  # 60 fresh held max (18:2x), 20 a run
    assert dyishappy_food_lines([(5, SKU)], {SKU: ITEM}, inv, NOW, {SKU}) == []
    assert dyishappy_food_lines([(5, SKU)], {}, inv, NOW, set()) == []  # overpriced
    full: tuple[Unit, ...] = tuple(
        food(f"x{i}", 0.3 * SHELF) for i in range(DYIS_STOP_UNITS)
    )
    big = Inventory(round=3, balance=10**9, units=full)
    assert dyishappy_food_lines([(5, SKU)], {SKU: ITEM}, big, NOW, set()) == []


def test_open_skus_skip_the_200_hold_cap_for_fresh_units() -> None:
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        max_units=6000,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        food_share=1.0,
        min_food_shelf_hours=168,
    )
    held = tuple(food(f"x{i}", 0.4 * SHELF) for i in range(210))

    def buy(opened: frozenset[str]) -> int:
        lines, _ = plan(
            Inventory(round=3, balance=900_000_000, units=held),
            {SKU: ITEM},
            (),
            policy,
            RestockState(),
            NOW,
            DemandSignal({}, {}),
            {SKU: (500.0, 60), **{f"o{i}": (9000.0, 10) for i in range(4)}},
            fresh_skus={SKU: 2.0},
            fresh_open=opened,
        )
        return sum(int(str(r["qty"])) for r in lines if r["sku"] == SKU)

    assert buy(frozenset()) == 0  # 210 held: FRESH_MAX_HELD stops it
    assert buy(frozenset({SKU})) == 20
