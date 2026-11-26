"""Patch 3.6: Saved is the share of the seller's margin we kept, not the discount."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from typing import TYPE_CHECKING

import pytest

from r3.buyer import MARGIN_LINE, known_cost_ratio
from r3.emergency_buyer import BREAK_RISK, PurchaseValue, receipt_unit, unit_cost

if TYPE_CHECKING:
    from pathlib import Path

LIST = 100_000


def value(
    kind: str, grade: str | None, price: int, hours: float = 0.0
) -> PurchaseValue:
    """Price one unit under the patch 3.6 line."""
    return PurchaseValue(kind, grade, hours, LIST, price, 1.0, 0.34, MARGIN_LINE)


def test_margin_share_follows_the_notice_formula() -> None:
    # A food unit costs about 52 % of list: paying 64 % keeps 36 of 48 points.
    assert value("food", "A", 64_000).margin_share() == pytest.approx(0.36 / 0.48)
    # Paying at or under the estimated cost counts as 1, as the notice caps it.
    assert value("goods", "A", 60_000).margin_share() == 1.0


def test_a_bedding_wall_at_seventy_percent_now_passes_for_grade_a() -> None:
    # 11/12 18:19 onward: sellers stopping at 0.70 of list were refused under
    # the 0.60 naked-A cap; with an A cost near 0.65 the deal keeps 0.86 of the
    # seller's margin, minus the break risk, above the 0.75 line.
    naked = value("goods", "A", 70_000)
    assert naked.acceptable()
    line = naked.max_price()
    assert line is not None
    assert 70_000 <= line <= 72_000
    # A unit with a full warranty may cost a little more.
    covered = value("goods", "A", 70_000, hours=6).max_price()
    assert covered is not None
    assert covered > line


def test_the_line_is_tighter_for_cheap_grades_and_food() -> None:
    b_line = value("goods", "B", 50_000, hours=6).max_price()
    food_line = value("food", "A", 50_000).max_price()
    assert b_line is not None
    assert food_line is not None
    assert b_line < 62_000
    assert 63_000 <= food_line <= 64_000
    # B without a full warranty and grade C stay excluded.
    assert value("goods", "B", 40_000).max_price() is None
    assert "C" not in BREAK_RISK
    assert value("goods", "C", 30_000).max_price() is None


def test_without_the_margin_line_the_old_discount_rule_still_holds() -> None:
    old = PurchaseValue("goods", "A", 0.0, LIST, 70_000, 1.0, 0.34)
    assert not old.acceptable()
    line = old.max_price()
    assert line is not None
    assert line <= 60_000


def supply_ledger(path: Path, units: list[str], total: int) -> Path:
    """Build a public ledger holding one supply lot."""
    with sqlite3.connect(path) as con:
        _ = con.execute(
            """CREATE TABLE rows (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT,
            round INTEGER, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER,
            grade TEXT, claim TEXT, claim_honest INTEGER, data TEXT)"""
        )
        _ = con.execute(
            "INSERT INTO rows (ts, kind, shop, sku, data) "
            "VALUES (?, 'supply', 's', '', ?)",
            (
                time.time() - 3600,
                json.dumps({"shop": "s", "sku": "G1", "total": total, "units": units}),
            ),
        )
    return path


def test_a_named_unit_is_priced_at_its_real_lot_cost(tmp_path: Path) -> None:
    db = supply_ledger(tmp_path / "l.sqlite", ["u-aa", "u-bb"], 144_000)
    assert unit_cost(db, "u-bb", time.time()) == 72_000
    assert unit_cost(db, "u-cc", time.time()) is None
    assert unit_cost(db, "not-a-unit", time.time()) is None
    assert receipt_unit({"unit": "u-aa"}) == "u-aa"
    assert receipt_unit({"cert": {"cert": {"unit": "u-bb", "grade": "A"}}}) == "u-bb"
    assert receipt_unit({"price": 1}) is None


def test_the_real_cost_moves_the_line_both_ways() -> None:
    estimate = value("goods", "A", 70_000).max_price()
    dear = dataclasses.replace(value("goods", "A", 70_000), known_cost=0.74)
    cheap = dataclasses.replace(value("goods", "A", 70_000), known_cost=0.50)
    assert estimate is not None
    dear_line = dear.max_price()
    cheap_line = cheap.max_price()
    assert dear_line is not None
    assert cheap_line is not None
    # A unit that cost 0.74 of list may be bought up to about 0.78; one that
    # cost 0.50 only up to about 0.61, where the estimate allowed 0.71.
    assert cheap_line < estimate < dear_line
    assert dear.acceptable()
    assert not cheap.acceptable()
    assert known_cost_ratio("u-x", LIST, lambda _u: 74_000) == 0.74
    assert known_cost_ratio("u-x", LIST, lambda _u: None) is None
    assert known_cost_ratio(None, LIST, lambda _u: 74_000) is None
