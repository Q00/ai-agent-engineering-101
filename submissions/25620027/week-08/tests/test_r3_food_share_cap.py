"""Food accept lines leave the buyer 0.78 of our margin (patch 3.6 buyers)."""

from __future__ import annotations

import pytest

from r3.pricing import FOOD_BUYER_SHARE, Quote, buyer_share_capped


def quoted(cost: int, margin: float = 0.18, *, clearance: bool = False) -> Quote:
    """Price a food unit the way quote() does: accept cost x 1.18, floor x 1.05."""
    return Quote(
        ask=9_000,
        accept_at=round(cost * (1 + margin)),
        floor=round(cost * 1.05),
        grade_now="A",
        clearance=clearance,
        stale=False,
    )


def share(price: int, cost: int, list_price: int = 10_000) -> float:
    """Return the buyer's patch 3.6 score for one deal."""
    return (list_price - price) / (list_price - cost)


def test_a_dear_food_unit_comes_down_to_the_buyer_share() -> None:
    capped = buyer_share_capped(quoted(7_000), 7_000, 10_000)
    assert capped.accept_at == 7_660
    assert share(capped.accept_at, 7_000) == pytest.approx(FOOD_BUYER_SHARE)
    assert capped.ask == 9_000


def test_a_cheap_food_unit_keeps_its_higher_line() -> None:
    before = quoted(5_000)
    assert share(before.accept_at, 5_000) > FOOD_BUYER_SHARE
    assert buyer_share_capped(before, 5_000, 10_000) is before


def test_the_cost_floor_and_clearance_stay_untouched() -> None:
    # A unit costing 0.9 of list cannot leave 0.78 above cost x 1.05.
    assert buyer_share_capped(quoted(9_000), 9_000, 10_000).accept_at == 9_450
    sale = quoted(7_000, clearance=True)
    assert buyer_share_capped(sale, 7_000, 10_000) is sale
