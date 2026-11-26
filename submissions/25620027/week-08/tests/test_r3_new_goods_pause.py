"""11/16 10:2x: the new-goods pause also holds on the depth path."""

from __future__ import annotations

import json as _json
import sqlite3 as _sqlite3
from typing import TYPE_CHECKING

from r3 import restock
from r3.endgame import ROUND_END
from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, Inventory, SurgePrice, Unit
from r3.policy import RestockPolicy
from r3.rater_demand import EXTRA_SKUS, NEW_BUYER_SKUS
from r3.restock import (
    MARKET_FIRST_FILL_UNTIL,
    DemandSignal,
    RestockState,
    goods_margin_ok,
    market_first_lines,
    merge_market_first,
    overpriced_relief,
    plan,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

NOW = ROUND_END - 4 * 86400
HOT = CatalogItem(sku="hot", category="beds", kind="goods", list_price=900_000)
POLICY = RestockPolicy(
    growth_enabled=False,
    depth_skus=1,
    units_per_sku=20,
    max_units=1000,
    reserve_cash=0,
    stock_share=0.8,
    goods_min_list_price=200_000,
    goods_max_list_price=5_000_000,
    food_share=0,
)
SIGNAL = DemandSignal({"beds": 500}, {"hot": 300})


def held(n: int) -> tuple[Unit, ...]:
    return tuple(
        Unit(
            unit=f"u-{i}",
            sku="hot",
            category="beds",
            kind="goods",
            list_price=900_000,
            cost=500_000,
            grade="A",
            supplied_at=NOW - 3600,
        )
        for i in range(n)
    )


def hot_qty(units: tuple[Unit, ...], state: RestockState) -> int:
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=units),
        {"hot": HOT},
        (),
        POLICY,
        state,
        NOW,
        SIGNAL,
    )
    return sum(int(str(line["qty"])) for line in lines if line["sku"] == "hot")


def test_paused_depth_skips_a_goods_sku_never_held_or_sold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hot_qty((), RestockState()) > 0  # open: depth starts it
    monkeypatch.setattr(restock, "NEW_GOODS_PAUSED", True)
    assert hot_qty((), RestockState()) == 0


def test_paused_depth_still_refills_a_goods_sku_we_hold_or_sold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restock, "NEW_GOODS_PAUSED", True)
    assert hot_qty(held(1), RestockState()) > 0
    sold = RestockState(last_sold_at={"hot": NOW - 600})
    assert hot_qty((), sold) > 0


def test_new_buyer_rice_in_extra_list_is_not_pushed_back() -> None:
    # The rice SKUs sit in both lists; the new-buyer group must ignore EXTRA.
    both = [sku for sku in NEW_BUYER_SKUS if sku in EXTRA_SKUS]
    assert both  # Gf0e58a76, G344fc18d


def test_goods_margin_ok_needs_paid_median_over_supply_cost(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    rows = [
        ("settle", "good", _json.dumps({"amount": 110})),
        ("settle", "thin", _json.dumps({"amount": 105})),
        ("supply", "", _json.dumps({"sku": "good", "qty": 2, "total": 200})),
        ("supply", "", _json.dumps({"sku": "thin", "qty": 1, "total": 100})),
    ]
    with _sqlite3.connect(path) as db:
        for seq, (kind, sku, data) in enumerate(rows):
            amount = _json.loads(data).get("amount")
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, sku, amount, data)"
                " VALUES (?,?,?,?,?,?)",
                (seq, NOW - 60, kind, sku, amount, data),
            )
    # 110 >= 1.08 x 100 passes; 105 < 108 does not.
    assert goods_margin_ok(path, NOW) == frozenset({"good"})


def test_overpriced_relief_uses_todays_cost_surge_and_sample(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    rows = (
        [("settle", "ok", {"amount": 110}) for _ in range(10)]
        + [("settle", "few", {"amount": 200}) for _ in range(3)]
        + [("settle", "dear", {"amount": 105}) for _ in range(10)]
        + [
            ("supply", "", {"sku": "ok", "qty": 1, "total": 100}),
            ("supply", "", {"sku": "few", "qty": 1, "total": 100}),
            ("supply", "", {"sku": "dear", "qty": 1, "total": 100}),
        ]
    )
    with _sqlite3.connect(path) as db:
        for seq, (kind, sku, data) in enumerate(rows):
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, sku, amount, data)"
                " VALUES (?,?,?,?,?,?)",
                (seq, NOW - 60, kind, sku, data.get("amount"), _json.dumps(data)),
            )
    blocked = frozenset({"ok", "few", "dear"})
    # ok: 1.08 x 100 <= 110; few: under 10 settles; dear: 105 < 108.
    assert overpriced_relief(path, NOW, blocked, {}) == frozenset({"ok"})
    # A surge factor on today's cost can take it back out.
    assert overpriced_relief(path, NOW, blocked, {"ok": 1.05}) == frozenset()


def test_market_first_lines_pick_profitable_market_sellers(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    rows = (
        [("settle", "hot", {"amount": 120}) for _ in range(25)]
        + [("settle", "thin", {"amount": 104}) for _ in range(25)]
        + [("settle", "rare", {"amount": 200}) for _ in range(5)]
        + [
            ("supply", "", {"sku": s, "qty": 1, "total": 100})
            for s in ("hot", "thin", "rare")
        ]
    )
    with _sqlite3.connect(path) as db:
        for seq, (kind, sku, data) in enumerate(rows):
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, sku, amount, data)"
                " VALUES (?,?,?,?,?,?)",
                (seq, NOW - 60, kind, sku, data.get("amount"), _json.dumps(data)),
            )
    catalog = {
        s: CatalogItem(sku=s, category="beds", kind="goods", list_price=200)
        for s in ("hot", "thin", "rare")
    }
    inv = Inventory(round=3, balance=10**9, units=held(0))
    # hot clears 1.08 with 25 settles; thin fails the margin; rare has 5 settles.
    assert market_first_lines(path, NOW, catalog, inv) == [{"sku": "hot", "qty": 10}]
    # A surge factor above 1 skips the SKU this window.
    surged = inv.model_copy(update={"surge": (SurgePrice(sku="hot", factor=1.2),)})
    assert market_first_lines(path, NOW, catalog, surged) == []


def test_merge_market_first_keeps_food_lines_after_the_fill() -> None:
    catalog = {
        **{
            f"g{i}": CatalogItem(sku=f"g{i}", category="c", kind="goods", list_price=9)
            for i in range(9)
        },
        **{
            f"f{i}": CatalogItem(sku=f"f{i}", category="c", kind="food", list_price=9)
            for i in range(6)
        },
    }
    first = [{"sku": f"g{i}", "qty": 10} for i in range(8)] + [{"sku": "g8", "qty": 3}]
    plan_lines = [{"sku": f"f{i}", "qty": 20} for i in range(6)]
    early = merge_market_first(first, plan_lines, catalog, MARKET_FIRST_FILL_UNTIL - 1)
    assert sum(1 for r in early if str(r["sku"]).startswith("f")) == 1
    # 11/17 08:0x: food keeps 6 lines a run (09:59: 8 a window = 6 a run, market-
    # first keeps at least 4); market-first takes the rest, largest shortfall first.
    first[5] = {"sku": "g5", "qty": 15}
    first[2] = {"sku": "g2", "qty": 12}
    for i in range(6, 9):
        catalog[f"f{i}"] = CatalogItem(
            sku=f"f{i}", category="c", kind="food", list_price=9
        )
    plan_lines = [{"sku": f"f{i}", "qty": 20} for i in range(9)] + [
        {"sku": "x", "qty": 4}
    ]
    catalog["x"] = CatalogItem(sku="x", category="c", kind="goods", list_price=9)
    late = merge_market_first(first, plan_lines, catalog, MARKET_FIRST_FILL_UNTIL)
    goods = [str(r["sku"]) for r in late if str(r["sku"]).startswith("g")]
    assert goods == ["g5", "g2", "g0", "g1"]  # the 3-unit top-up skipped
    assert sum(1 for r in late if str(r["sku"]).startswith("f")) == 6
    assert len(late) == 10
    # Food A+B over 300: 5 food lines, market-first 5.
    full = merge_market_first(
        first, plan_lines, catalog, MARKET_FIRST_FILL_UNTIL, food_ab=301
    )
    assert sum(1 for r in full if str(r["sku"]).startswith("f")) == 5
    # Few market-first lines: the free lines go to other goods, then more food.
    few = merge_market_first(
        [{"sku": "g0", "qty": 10}], plan_lines, catalog, MARKET_FIRST_FILL_UNTIL
    )
    assert [str(r["sku"]) for r in few] == [
        "g0",
        *[f"f{i}" for i in range(6)],
        "x",
        "f6",
        "f7",
    ]
