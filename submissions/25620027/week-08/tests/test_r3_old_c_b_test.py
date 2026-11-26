"""11/17 07:5x: plain-form goods asks get C first; B +5 % for two forms (2 h test)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from r3 import seller
from r3.market_files import Unit
from r3.seller import (
    B_TEST_MARKUP,
    Quote,
    Request,
    b_test_markup,
    offerable_units,
    pick_unit,
)
from tests.r3_stock import LAMP, MILK, unit

if TYPE_CHECKING:
    import pytest

NOW = time.time()
A_UNIT = Unit.model_validate(unit(LAMP, 1, grade="A", cost=14_000, now=NOW))
C_UNIT = Unit.model_validate(unit(LAMP, 3, grade="C", cost=16_000, now=NOW))
PLAIN = f"I want to buy lamp ({LAMP})"
CREDITS = f"I want to buy {LAMP}. Please quote a price in credits."
CHEAPEST_IN_TOWN = (
    f"Please quote one lamp ({LAMP}) with the market certificate. Please state "
    "grade A/B and any breakage refund warranty."
)


def picked(text: str) -> Unit | None:
    units = offerable_units((A_UNIT, C_UNIT), text)
    return pick_unit(units, Request((LAMP,), None, text), NOW, set())


def test_plain_goods_ask_gets_the_c_unit_first() -> None:
    assert picked(PLAIN) is C_UNIT
    # fair-furniture's form stays A first, C excluded.
    assert picked(CREDITS) is A_UNIT


def test_plain_food_ask_is_untouched() -> None:
    food_a = Unit.model_validate(unit(MILK, 1, grade="A", cost=1_000, now=NOW))
    food_c = Unit.model_validate(unit(MILK, 3, grade="C", cost=900, now=NOW))
    text = f"I want to buy milk ({MILK})"
    got = pick_unit((food_a, food_c), Request((MILK,), None, text), NOW, set())
    assert got is food_a


def test_b_test_markup_is_off_after_the_10_08_judgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert B_TEST_MARKUP == 1.0
    b = Quote(20_000, 18_000, 17_000, "B", clearance=False, stale=False)
    assert b_test_markup(A_UNIT, b, PLAIN) is b
    # The step still works if it is put back.
    monkeypatch.setattr(seller, "B_TEST_MARKUP", 1.05)
    for text in (PLAIN, CHEAPEST_IN_TOWN):
        up = b_test_markup(A_UNIT, b, text)
        assert (up.ask, up.accept_at, up.floor) == (21_000, 18_900, 17_000)
    assert b_test_markup(A_UNIT, b, CREDITS) is b
    a = Quote(20_000, 18_000, 17_000, "A", clearance=False, stale=False)
    assert b_test_markup(A_UNIT, a, PLAIN) is a
