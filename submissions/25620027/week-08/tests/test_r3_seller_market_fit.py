"""A same-SKU unit that clears the market replaces one whose floor cannot."""

from __future__ import annotations

import time

from r3.market_files import Unit
from r3.policy import SellerPolicy
from r3.pricing import quote
from r3.seller import market_fit_unit
from tests.r3_stock import LAMP, unit

NOW = time.time()
POLICY = SellerPolicy()
COSTLY_A = Unit.model_validate(unit(LAMP, 1, grade="A", cost=34_000, now=NOW))
CHEAP_B = Unit.model_validate(unit(LAMP, 2, grade="B", cost=20_000, now=NOW))
CHEAP_C = Unit.model_validate(unit(LAMP, 3, grade="C", cost=14_000, now=NOW))
STOCK = (COSTLY_A, CHEAP_B, CHEAP_C)
PLAIN = f"I want to buy lamp ({LAMP})"


def floor(u: Unit) -> int:
    return quote(u, NOW, None, POLICY).accept_at


def test_costly_a_gives_way_to_a_b_unit_that_clears_the_median() -> None:
    median = 30_000.0
    assert floor(COSTLY_A) > median >= floor(CHEAP_B)
    chosen = market_fit_unit(COSTLY_A, STOCK, set(), NOW, POLICY, median, PLAIN)
    assert chosen is CHEAP_B


def test_buyer_naming_grade_a_alone_keeps_the_a_unit() -> None:
    text = f"I want to buy lamp ({LAMP}), grade A please"
    chosen = market_fit_unit(COSTLY_A, STOCK, set(), NOW, POLICY, 30_000.0, text)
    assert chosen is COSTLY_A
    both = f"I want to buy lamp ({LAMP}), grade A or B"
    assert market_fit_unit(COSTLY_A, STOCK, set(), NOW, POLICY, 30_000.0, both) is (
        CHEAP_B
    )


def test_c_only_when_the_buyer_accepts_it() -> None:
    no_b = {CHEAP_B.unit}
    assert (
        market_fit_unit(COSTLY_A, STOCK, no_b, NOW, POLICY, 30_000.0, PLAIN) is COSTLY_A
    )
    takes_c = f"I want to buy lamp ({LAMP}), grade C is fine"
    assert (
        market_fit_unit(COSTLY_A, STOCK, no_b, NOW, POLICY, 30_000.0, takes_c)
        is CHEAP_C
    )


def test_no_switch_without_a_median_or_when_the_unit_already_clears() -> None:
    assert market_fit_unit(COSTLY_A, STOCK, set(), NOW, POLICY, None, PLAIN) is COSTLY_A
    high = float(floor(COSTLY_A))
    assert market_fit_unit(COSTLY_A, STOCK, set(), NOW, POLICY, high, PLAIN) is COSTLY_A


def test_a_dear_a_unit_gives_way_to_a_cheaper_a_unit_the_anchor_can_clear() -> None:
    # 11/12: A units at 0.76 and 0.60 of list, median 0.625 of list. The cheap
    # unit's raw quote (0.648) is above the median, but after the market anchor
    # it accepts at its cost x 1.02 floor (0.612): it replaces the dear one.
    dear = Unit.model_validate(unit(LAMP, 4, grade="A", cost=30_400, now=NOW))
    cheap = Unit.model_validate(unit(LAMP, 5, grade="A", cost=24_000, now=NOW))
    median = 25_000.0
    assert floor(cheap) > median
    chosen = market_fit_unit(dear, (dear, cheap), set(), NOW, POLICY, median, PLAIN)
    assert chosen is cheap
