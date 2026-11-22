"""Restock stops buying SKUs the market sells below what they cost us."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

from r3.overpriced import (
    OVERPRICED_FILE,
    find_overpriced,
    load_overpriced,
    overpriced,
    write_overpriced,
)
from r3.restock import DemandSignal, run
from tests.test_r3_priority_restock import NOW, RESULT, SKU, prepare
from tests.test_r3_stockouts import OURS, ledger

if TYPE_CHECKING:
    from pathlib import Path


def test_market_median_below_our_cost_blocks_unless_we_still_sell_above_cost() -> None:
    market = {"couch": [2_100_000] * 6, "bed": [1_500_000] * 6, "thin": [10] * 4}
    costs = {"couch": [2_390_000], "bed": [1_550_000], "thin": [99]}
    # Given: we sold the bed above cost three times in 48 h; the couch never.
    rows = overpriced(market, costs, {"bed": 3})
    # Then: only the couch is blocked; four settles are no market price.
    assert set(rows) == {"couch"}
    assert rows["couch"]["our_cost"] == 2_390_000
    assert rows["couch"]["market_median"] == 2_100_000


def test_ledger_reading_uses_our_lots_and_counts_our_profitable_sales(
    tmp_path: Path,
) -> None:
    now = time.time()
    h = 3600
    lot = {"sku": "couch", "units": ["u1", "u2"], "total": 4_780_000}
    rival = [
        (now - i * h, "settle", "https://rival", "couch", {"unit": f"r{i}"})
        for i in range(1, 6)
    ]
    db = ledger(
        tmp_path / "ledger.sqlite",
        [(now - 60 * h, "supply", "me", "", lot), *rival],
    )
    con = sqlite3.connect(db)
    con.execute("update rows set amount=2100000 where kind='settle'")
    con.commit()
    con.close()
    rows = find_overpriced(db, OURS, "me", now)
    assert set(rows) == {"couch"}
    assert rows["couch"]["our_cost"] == 2_390_000


def test_stale_or_missing_list_blocks_nothing(tmp_path: Path) -> None:
    path = tmp_path / OVERPRICED_FILE
    assert load_overpriced(path, 0) == frozenset()
    write_overpriced(path, {"couch": {"our_cost": 1.0}}, 100.0)
    assert load_overpriced(path, 200.0) == frozenset({"couch"})
    assert load_overpriced(path, 100.0 + 7200) == frozenset()


def test_real_restock_hook_skips_a_blocked_sku(tmp_path: Path) -> None:
    # Given: the hook would buy three units of SKU (owner allocation).
    folder = prepare(tmp_path)
    before = RESULT.validate_json(run(folder, signal=DemandSignal({}, {}), now=NOW))
    assert before["buy"]
    # When: the market sells SKU below our cost.
    (tmp_path / "second").mkdir()
    folder2 = prepare(tmp_path / "second")
    write_overpriced(tmp_path / "second" / OVERPRICED_FILE, {SKU: {}}, NOW)
    after = RESULT.validate_json(run(folder2, signal=DemandSignal({}, {}), now=NOW))
    # Then: the hook buys none of it.
    assert all(line.sku != SKU for line in after["buy"])
