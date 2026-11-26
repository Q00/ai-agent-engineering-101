"""11/17 20:00 (owner): the tao form gets units it can buy at list x 0.62 or less."""

from __future__ import annotations

import time

from r3.market_files import Unit
from r3.seller import Quote, Request, offerable_units, pick_unit, tao_quote
from tests.r3_stock import MILK, unit

NOW = time.time()
TEXT = (
    f"I want to buy milk ({MILK}). I need at least 10 h of shelf life left; "
    "please ship a unit that fresh."
)


def milk(n: int, grade: str, cost: int) -> Unit:
    return Unit.model_validate(
        unit(MILK, n, grade=grade, cost=cost, now=NOW)
    ).model_copy(update={"list_price": 10_000})


def test_tao_form_offers_cheap_units_and_food_b_c_first() -> None:
    a = milk(1, "A", 5_000)
    b = milk(2, "B", 5_500)
    dear = milk(3, "B", 6_500)  # cost over 0.62 of list: not offered
    units = offerable_units((a, b, dear), TEXT)
    assert dear not in units
    assert pick_unit(units, Request((MILK,), None, TEXT), NOW, set()) is b
    assert (
        pick_unit(
            offerable_units((dear,), TEXT), Request((MILK,), None, TEXT), NOW, set()
        )
        is None
    )


def test_tao_quote_caps_the_price_at_062_of_list_never_under_cost() -> None:
    b = milk(2, "B", 5_500)
    q = Quote(7_000, 6_600, 5_700, "B", clearance=False, stale=False)
    got = tao_quote(b, q, TEXT)
    assert (got.ask, got.accept_at, got.floor) == (6_200, 6_200, 5_700)
    plain = f"I want to buy milk ({MILK})"
    assert tao_quote(b, q, plain) is q
