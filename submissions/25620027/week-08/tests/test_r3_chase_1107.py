"""11/17 11:07 (owner): three 2 h tests to chase the top book.

Goods C asks +10 %, dear slow sellers enter market-first with a 6-unit hold, and
SKUs we sell out of hold twice the market rate (20 at least).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.market_files import Inventory, Unit
from r3.restock import DEAR_HOLD, DEEP_HOLD_MIN, market_first_lines
from r3.seller import C_GOODS_ASK_UP, Quote, c_goods_ask
from tests.r3_stock import LAMP, unit
from tests.test_r3_book_defence_0807 import NOW, goods, ledger

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_goods_c_ask_markup_is_back_to_one_after_13_08(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from r3 import seller  # noqa: PLC0415 - local to this test

    assert C_GOODS_ASK_UP == 1.0
    lamp = Unit.model_validate(unit(LAMP, 1, grade="C", cost=10_000, now=NOW))
    c = Quote(20_000, 15_000, 10_500, "C", clearance=False, stale=False)
    assert c_goods_ask(lamp, c) is c
    monkeypatch.setattr(seller, "C_GOODS_ASK_UP", 1.10)
    got = c_goods_ask(lamp, c)
    assert (got.ask, got.accept_at, got.floor) == (22_000, 15_000, 10_500)


def test_warranty_over_one_hour_raises_the_goods_accept_line() -> None:
    from r3.seller import asked_warranty, warranty_floor  # noqa: PLC0415

    lamp = Unit.model_validate(unit(LAMP, 1, grade="A", cost=1_000_000, now=NOW))
    q = Quote(1_300_000, 1_050_000, 1_050_000, "A", clearance=False, stale=False)
    text = "I want to buy sofa (G1234abcd). Please include a 6-hour warranty."
    hours = asked_warranty(text)
    assert hours is not None
    assert hours > 1
    got = warranty_floor(lamp, q, hours)
    assert (got.accept_at, got.floor) == (1_120_000, 1_120_000)
    cheap = lamp.model_copy(update={"cost": 100_000})
    small = Quote(130_000, 105_000, 105_000, "C", clearance=False, stale=False)
    assert warranty_floor(cheap, small, 6).accept_at == 110_000
    assert warranty_floor(lamp, q, 1) is q
    assert warranty_floor(lamp, q, None) is q


def test_dear_slow_goods_enter_with_a_small_hold(tmp_path: Path) -> None:
    # dear: 10 settles in 6 h at 120 on 100 cost (x1.20); cheap: 10 at 106.
    path = ledger(tmp_path, {"dear": (10, 120), "cheap": (10, 106)})
    inv = Inventory(round=3, balance=10**9, units=())
    lines = market_first_lines(path, NOW, goods("dear", "cheap"), inv)
    assert lines == [{"sku": "dear", "qty": DEAR_HOLD}]


def test_sold_out_skus_hold_deeper(tmp_path: Path) -> None:
    # 24 settles in 6 h (4 an hour): hold 10 normally, 20 after 4+ sales in 2 h
    # with no A unit bought today on hand.
    path = ledger(tmp_path, {"hot": (24, 130)})
    inv = Inventory(round=3, balance=10**9, units=())
    assert market_first_lines(path, NOW, goods("hot"), inv) == [
        {"sku": "hot", "qty": 10}
    ]
    sold = {"hot": [NOW - 600] * 4}
    assert market_first_lines(path, NOW, goods("hot"), inv, sold) == [
        {"sku": "hot", "qty": DEEP_HOLD_MIN}
    ]
