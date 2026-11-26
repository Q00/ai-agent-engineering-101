"""11/17 17:06 (owner): rebuy goods whose B/C made money today."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from r3.bc_rebuy import BC_DAY_START, BC_STATE_FILE, apply_bc_rebuy, bc_rebuy_lines
from r3.events import EventLedger
from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, Inventory, Unit
from tests.r3_stock import LAMP, unit

if TYPE_CHECKING:
    from pathlib import Path

SHOP = "https://ax.test"
NOW = BC_DAY_START + 9 * 3600  # 16:10 KST


def setup(tmp_path: Path) -> tuple[Path, dict[str, CatalogItem]]:
    _ = LedgerDB(tmp_path / "r3-ledger.sqlite")
    events = EventLedger(tmp_path / "r3-events.sqlite")
    rows: list[tuple[str, str, str, dict[str, object]]] = []
    for i in range(6):  # sofa: 6 B/C sales at 1.5M on 1.0M cost = 3.0M net
        u = f"s{i}"
        rows.append(("settle", "sofa", "B", {"unit": u, "amount": 1_500_000}))
        events.record_receipt(
            task=u,
            unit=u,
            sku="sofa",
            price=1_500_000,
            cost=1_000_000,
            grade="B",
            claim="",
            warranty_hours=1,
            rounds=1,
        )
    rows.append(("supply", "", "", {"sku": "sofa", "qty": 10, "total": 10_000_000}))
    rows.append(("settle", "zin", "C", {"unit": "z0", "amount": 3_000_000}))
    events.record_receipt(
        task="z0",
        unit="z0",
        sku="zin",
        price=3_000_000,
        cost=1_000_000,
        grade="C",
        claim="",
        warranty_hours=1,
        rounds=1,
    )
    rows.append(("supply", "", "", {"sku": "zin", "qty": 2, "total": 2_000_000}))
    with sqlite3.connect(tmp_path / "r3-ledger.sqlite") as db:
        for seq, (kind, sku, grade, data) in enumerate(rows):
            shop = "25620027" if kind == "supply" else SHOP + "/"
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, shop, sku, grade, amount, data)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    seq,
                    NOW - 3600,
                    kind,
                    shop,
                    sku,
                    grade,
                    data.get("amount"),
                    json.dumps(data),
                ),
            )
    catalog = {
        "sofa": CatalogItem(
            sku="sofa", name="소파", kind="goods", list_price=2_000_000
        ),
        "zin": CatalogItem(
            sku="zin", name="지누스 매트", kind="goods", list_price=2_000_000
        ),
    }
    return tmp_path, catalog


def test_bc_rebuy_buys_ten_once_an_hour_and_skips_zinus(tmp_path: Path) -> None:
    data, catalog = setup(tmp_path)
    inv = Inventory(round=3, balance=10**9, units=())
    lines = bc_rebuy_lines(data, SHOP, NOW, catalog, inv, 1e12, 1e12)
    assert lines == [{"sku": "sofa", "qty": 10}]
    state = json.loads((data / BC_STATE_FILE).read_text("utf-8"))
    assert state["spent"] == 10_000_000
    # Same hour: not again.
    assert bc_rebuy_lines(data, SHOP, NOW + 1200, catalog, inv, 1e12, 1e12) == []


def test_bc_rebuy_waits_while_bc_units_are_held(tmp_path: Path) -> None:
    data, catalog = setup(tmp_path)
    held = tuple(
        Unit.model_validate(unit(LAMP, i + 1, grade="B", now=NOW)).model_copy(
            update={"sku": "sofa", "unit": f"h{i}", "supplied_at": NOW - 600}
        )
        for i in range(3)
    )
    inv = Inventory(round=3, balance=10**9, units=held)
    assert bc_rebuy_lines(data, SHOP, NOW, catalog, inv, 1e12, 1e12) == []


def test_apply_keeps_food_lines() -> None:
    catalog = {
        "f": CatalogItem(sku="f", kind="food", list_price=9),
        "g": CatalogItem(sku="g", kind="goods", list_price=9),
    }
    lines = [{"sku": "g", "qty": 5}] * 6 + [{"sku": "f", "qty": 5}] * 4
    out = apply_bc_rebuy(lines, [{"sku": "r", "qty": 10}], catalog, 10, 6)
    assert out[0] == {"sku": "r", "qty": 10}
    assert sum(1 for r in out if r["sku"] == "f") == 4
    assert len(out) == 10


def test_bc_rebuy_excludes_skus_whose_bc_did_not_sell() -> None:
    from r3.bc_rebuy import BC_EXCLUDED  # noqa: PLC0415 - local to this test

    assert "G2698c6f8" in BC_EXCLUDED
