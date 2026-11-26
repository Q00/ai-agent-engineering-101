"""Buyers who rate goods C well get a same-SKU C unit first; others do not."""

from __future__ import annotations

import time

from r3.market_files import Unit
from r3.policy import SellerPolicy
from r3.seller import (
    TOTATO_C_MARKUP,
    Quote,
    Request,
    c_first_buyer,
    market_fit_unit,
    offerable_units,
    pick_unit,
    totato_c_markup,
)
from tests.r3_stock import LAMP, unit

NOW = time.time()
POLICY = SellerPolicy()
A_UNIT = Unit.model_validate(unit(LAMP, 1, grade="A", cost=14_000, now=NOW))
C_UNIT = Unit.model_validate(unit(LAMP, 3, grade="C", cost=16_000, now=NOW))
STOCK = (A_UNIT, C_UNIT)
TOTATO = (
    f"I want to buy lamp ({LAMP}). Quote the total price. Require a market "
    "certificate. Confirm available stock, true grade, total price and warranty."
)
CHEAPEST_IN_TOWN = (
    f"Please quote one lamp ({LAMP}) with the market certificate. Please state "
    "grade A/B and any breakage refund warranty."
)
PLAIN = f"I want to buy lamp ({LAMP})"
A_OR_B_ONLY = f"I want to buy lamp ({LAMP}), grade A or B only"
NO_C = f"I want to buy lamp ({LAMP}). The unit must last at least 48.0 hours."


def picked(text: str) -> Unit | None:
    units = offerable_units(STOCK, text)
    return pick_unit(units, Request((LAMP,), None, text), NOW, set())


def test_identified_c_buyers_get_the_c_unit_first() -> None:
    # Given: an A unit that is cheaper per list and a C unit of the same SKU.
    # When/Then: the two C-first openings get C; since 11/17 07:5x the plain goods
    # ask gets C too (test_r3_old_c_b_test.py).
    assert c_first_buyer(TOTATO)
    assert c_first_buyer(CHEAPEST_IN_TOWN)
    assert picked(TOTATO) is C_UNIT
    assert picked(CHEAPEST_IN_TOWN) is C_UNIT
    assert picked(PLAIN) is C_UNIT


def test_a_or_b_only_and_no_c_buyers_are_untouched() -> None:
    assert not c_first_buyer(A_OR_B_ONLY)
    assert not c_first_buyer(NO_C)
    assert picked(A_OR_B_ONLY) is A_UNIT
    assert picked(NO_C) is A_UNIT


def test_c_first_buyer_without_c_stock_gets_a() -> None:
    units = (A_UNIT,)
    assert pick_unit(units, Request((LAMP,), None, TOTATO), NOW, set()) is A_UNIT


def test_market_fit_keeps_the_c_unit_for_a_c_first_buyer() -> None:
    # A median below every floor would otherwise look for an A/B replacement.
    assert market_fit_unit(C_UNIT, STOCK, set(), NOW, POLICY, 1.0, TOTATO) is C_UNIT


def test_totato_c_goods_markup_is_off_since_18_2x() -> None:
    # 11/16 16:43 +5 % cut totato goods C settles 28 % in 1.6 h; back to 1.0.
    assert TOTATO_C_MARKUP == 1.0
    quote = Quote(20_000, 18_000, 17_000, "C", clearance=False, stale=False)
    assert totato_c_markup(C_UNIT, quote, TOTATO) is quote


def test_cheapest_in_town_food_gets_b_then_a_never_c() -> None:
    from r3.seller import CIT_FOOD_NO_C  # noqa: PLC0415 - local to this test
    from tests.r3_stock import MILK  # noqa: PLC0415

    assert CIT_FOOD_NO_C
    text = (
        f"Please quote one milk ({MILK}) with the market certificate. Please state "
        "grade A/B and any breakage refund warranty."
    )
    a = Unit.model_validate(unit(MILK, 1, grade="A", cost=900, now=NOW))
    b = Unit.model_validate(unit(MILK, 2, grade="B", cost=1_000, now=NOW))
    c = Unit.model_validate(unit(MILK, 3, grade="C", cost=500, now=NOW))

    def got(stock: tuple[Unit, ...]) -> Unit | None:
        return pick_unit(
            offerable_units(stock, text), Request((MILK,), None, text), NOW, set()
        )

    assert got((a, b, c)) is b
    assert got((a, c)) is a
    assert got((c,)) is None
    # Goods C still goes first for this form.
    assert picked(CHEAPEST_IN_TOWN) is C_UNIT


def test_cheapest_in_town_food_form_with_a_shelf_need_gets_no_c() -> None:
    from tests.r3_stock import MILK  # noqa: PLC0415

    text = (
        f"Please quote one milk ({MILK}) with the market certificate. "
        "It must last at least 10.25 hours."
    )
    b = Unit.model_validate(unit(MILK, 2, grade="B", cost=1_000, now=NOW))
    c = Unit.model_validate(unit(MILK, 3, grade="C", cost=500, now=NOW))
    units = offerable_units((b, c), text)
    assert c not in units
    assert pick_unit(units, Request((MILK,), None, text), NOW, set()) is b
