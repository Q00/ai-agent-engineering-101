"""Rater demand SKUs pass the goods price floor and follow our own sales."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Final

from r3 import restock
from r3.endgame import ROUND_END
from r3.market_files import CatalogItem, Inventory
from r3.policy import RestockPolicy
from r3.rater_demand import (
    EXTRA_SKUS,
    NEW_BUYER_SKUS,
    find_rater_demand,
    load_rater_demand,
    write_rater_demand,
)
from r3.restock import DemandSignal, RestockState, plan

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

NOW: Final = ROUND_END - 60 * 3600
PAD: Final = CatalogItem(
    sku="G575cddb8", category="침구", kind="goods", list_price=27_900
)
POLICY: Final = RestockPolicy(
    growth_enabled=False,
    units_per_sku=20,
    replenish_qty=10,
    depth_skus=1,
    seed_budget=0,
    reserve_cash=0,
    stock_share=0.8,
    goods_min_list_price=200_000,
    goods_max_list_price=5_000_000,
    food_share=0,
    probe_skus=("G575cddb8",),
)


def buy(rater: tuple[str, ...], sold_12h: int = 0) -> int:
    economics = {"G575cddb8": (500.0, sold_12h)} | {
        f"o{i}": (9_000.0, 10) for i in range(4)
    }
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=()),
        {"G575cddb8": PAD},
        (),
        POLICY,
        RestockState(),
        NOW,
        DemandSignal({}, {}),
        economics,
        rater_skus=rater,
    )
    return sum(int(str(r["qty"])) for r in lines if r["sku"] == "G575cddb8")


def test_listed_cheap_goods_are_trialled_then_follow_sales_past_the_probe_cap() -> None:
    assert buy(()) == 0  # under the goods price floor, not on the list
    assert buy(("G575cddb8",)) == 4
    assert buy(("G575cddb8",), sold_12h=6) == 12  # probe cap of 3 lifted


def test_rater_demand_counts_the_two_buyers_and_skips_the_pillow_cover(
    tmp_path: Path,
) -> None:
    db = tmp_path / "ledger.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "create table rows (kind text, buyer text, sku text, ts real, data text)"
    )
    rows = [("settle", "totato2203-r3", "A", NOW, "{}")] * 3 + [
        ("settle", "q00", "B", NOW, "{}"),
        ("settle", "q00", "Ga2e3075f", NOW, "{}"),
        ("settle", "someone", "C", NOW, "{}"),
    ]
    con.executemany("insert into rows values (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    # 11/15: the bjeon01 extras follow the rater buyers' own SKUs.
    new = [sku for sku in NEW_BUYER_SKUS if sku not in EXTRA_SKUS]
    assert find_rater_demand(db, NOW) == ["A", "B", *EXTRA_SKUS, *new]
    path = tmp_path / "rater.json"
    write_rater_demand(path, ["A"], NOW)
    assert load_rater_demand(path, NOW + 60) == ("A",)
    assert load_rater_demand(path, NOW + 3 * 3600) == ()
    assert json.loads(path.read_text("utf-8"))["skus"] == ["A"]


def test_new_goods_pause_stops_listed_goods_trials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 11/16 09:2x (owner): no goods SKU is started while the book gap is under 35M.
    monkeypatch.setattr(restock, "NEW_GOODS_PAUSED", True)
    assert buy(("G575cddb8",)) == 0
