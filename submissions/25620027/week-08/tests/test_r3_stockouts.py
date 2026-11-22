"""Missed sales at zero stock are measured from the ledger and size restock up."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

from r3.market_files import read_catalog, read_demand, read_inventory
from r3.policy import RestockPolicy
from r3.restock import DemandSignal, RestockState, plan
from r3.stockouts import (
    load_stockouts,
    missed_sales,
    stockout_target,
    write_stockouts,
)
from tests.r3_stock import CURTAIN, write_market

if TYPE_CHECKING:
    from pathlib import Path

OURS = "https://our.shop"


def ledger(
    path: Path, rows: list[tuple[float, str, str, str, dict[str, object]]]
) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "create table rows (seq integer primary key, ts real, kind text, round int,"
        " buyer text, shop text, sku text, amount int, grade text, claim text,"
        " claim_honest int, data text)"
    )
    for ts, kind, shop, sku, data in rows:
        con.execute(
            "insert into rows (ts, kind, shop, sku, data) values (?,?,?,?,?)",
            (ts, kind, shop, sku, json.dumps(data)),
        )
    con.commit()
    con.close()
    return path


def test_market_sales_while_we_hold_none_count_as_missed_by_our_share(
    tmp_path: Path,
) -> None:
    now = time.time()
    h = 3600
    # Given: we bought 2 curtains 5 h ago and sold both within the next 15 minutes.
    db = ledger(
        tmp_path / "ledger.sqlite",
        [
            (now - 5 * h, "supply", "me", "", {"sku": CURTAIN, "units": ["u1", "u2"]}),
            (now - 4.95 * h, "settle", "https://rival", CURTAIN, {"unit": "r0"}),
            (now - 4.9 * h, "settle", OURS, CURTAIN, {"unit": "u1"}),
            (now - 4.8 * h, "settle", OURS, CURTAIN, {"unit": "u2"}),
            # ...then the market kept selling it while we had none.
            (now - 3 * h, "settle", "https://rival", CURTAIN, {"unit": "r1"}),
            (now - 2 * h, "settle", "https://rival", CURTAIN, {"unit": "r2"}),
            (now - 1 * h, "settle", "https://rival", CURTAIN, {"unit": "r3"}),
        ],
    )
    rows = missed_sales(db, OURS, "me", {}, now, hours=6)
    row = rows[CURTAIN]
    # Then: we sold 2 of the 3 sales while stocked; 3 more went by at zero.
    assert row["at_zero"] == 3
    assert row["share"] == round(2 / 3, 3)
    assert row["missed"] == 2.0
    assert row["market_per_h"] == 1.0
    # And: restock reads only fresh reports, only SKUs that missed enough.
    report = tmp_path / "r3-stockouts.json"
    write_stockouts(report, rows, now)
    assert load_stockouts(report, now) == {CURTAIN: (1.0, round(2 / 3, 3))}
    assert load_stockouts(report, now + 7200) == {}


def test_a_stocked_out_sku_off_the_depth_list_gets_an_hour_of_our_sales(
    tmp_path: Path,
    r3_restock_now: float,
) -> None:
    now = r3_restock_now
    folder = write_market(tmp_path / ".market", [], balance=30_000_000)
    policy = RestockPolicy(
        goods_min_list_price=0, goods_max_list_price=1_000_000, units_per_sku=10
    )
    # Given: the curtain is not a market best seller, but we lead it when stocked.
    lines, _ = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        policy,
        RestockState(),
        now,
        DemandSignal({}, {}),
        stockouts={CURTAIN: (6.0, 0.5)},
    )
    # Then: it is bought for an hour of our sales plus one: ceil(6 x 0.5) + 1 = 4.
    assert {line["sku"]: line["qty"] for line in lines}.get(CURTAIN) == 4
    assert stockout_target(6.0, 0.5, 10) == 4
    assert stockout_target(100.0, 0.9, 10) == 10
