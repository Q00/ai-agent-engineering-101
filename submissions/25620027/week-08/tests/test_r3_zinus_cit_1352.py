"""11/17 13:52 (owner): Zinus A at cost x 1.12; cheapest-in-town unmet food asks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.fresh_list import load_cit_needs, record_cit_need
from r3.market_files import Inventory, Unit
from r3.restock import market_first_lines
from r3.seller import Quote, zinus_floor
from tests.r3_stock import LAMP, unit
from tests.test_r3_book_defence_0807 import NOW, goods, ledger

if TYPE_CHECKING:
    from pathlib import Path


def test_zinus_grade_a_accepts_no_lower_than_cost_x_112() -> None:
    lamp = Unit.model_validate(unit(LAMP, 1, grade="A", cost=100_000, now=NOW))
    zinus = lamp.model_copy(update={"name": "지누스 루미 매트리스"})
    q = Quote(130_000, 105_000, 105_000, "A", clearance=False, stale=False)
    got = zinus_floor(zinus, q)
    assert (got.accept_at, got.floor) == (112_000, 112_000)
    assert zinus_floor(lamp, q) is q
    b = Quote(130_000, 105_000, 105_000, "B", clearance=False, stale=False)
    assert zinus_floor(zinus, b) is b


def test_zinus_market_first_needs_112(tmp_path: Path) -> None:
    path = ledger(tmp_path, {"zin": (30, 110), "plain": (30, 110)})
    catalog = goods("zin", "plain")
    catalog["zin"] = catalog["zin"].model_copy(update={"name": "지누스 매트리스"})
    inv = Inventory(round=3, balance=10**9, units=())
    skus = [r["sku"] for r in market_first_lines(path, NOW, catalog, inv)]
    assert skus == ["plain"]  # 110/100 clears 1.05 but not Zinus 1.12


def test_cit_needs_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cit.json"
    record_cit_need(path, "G1", 72.25, NOW - 600)
    record_cit_need(path, "G1", 180.25, NOW - 300)
    record_cit_need(path, "G2", 42.0, NOW - 4 * 3600)  # past 3 h: dropped
    rate, need = load_cit_needs(path, NOW)
    assert need == {"G1": 180.25}
    assert round(rate["G1"], 3) == round(2 / 3, 3)
