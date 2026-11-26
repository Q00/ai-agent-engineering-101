"""Round 3 endgame: stop buying, then sell stock down before the final board."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from r3.endgame import HOUR, ROUND_END, buy_share, sell_pressure
from r3.market_files import Unit
from r3.policy import Policy, RestockPolicy
from r3.pricing import quote
from r3.restock import RestockState, plan, read_catalog, read_demand, read_inventory
from tests.r3_stock import CURTAIN, LAMP, MILK, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path


def test_buying_tapers_out_and_prices_slide_only_near_the_end() -> None:
    assert buy_share(ROUND_END - 100 * HOUR) == 1.0
    assert buy_share(ROUND_END - 36 * HOUR) == 1.0
    assert buy_share(ROUND_END - 12 * HOUR) == 1.0
    assert buy_share(ROUND_END - 1 * HOUR) == 1.0
    assert buy_share(ROUND_END - 0.625 * HOUR) == 0.5
    assert buy_share(ROUND_END - 0.25 * HOUR) == 0.0
    assert sell_pressure(ROUND_END - 30 * HOUR) == 0.0
    assert 0.0 < sell_pressure(ROUND_END - 12 * HOUR) < 1.0
    assert sell_pressure(ROUND_END - 1 * HOUR) == 1.0
    assert sell_pressure(ROUND_END + HOUR) == 1.0


def test_endgame_goods_keep_their_book_value_cost_floor() -> None:
    policy = Policy().seller
    early = ROUND_END - 48 * HOUR
    late = ROUND_END - 2 * HOUR
    mattress = Unit.model_validate(unit(CURTAIN, 1, grade="A", now=late))
    normal = quote(mattress, early, None, policy)
    final = quote(mattress, late, None, policy)
    assert normal.endgame == 0.0
    assert normal.accept_at > mattress.cost
    assert final.endgame == 1.0
    assert final.floor == normal.floor
    assert final.accept_at == normal.accept_at
    assert final.floor == round(mattress.cost * (1 + policy.min_margin))
    # 11/17 07:3x: goods keep their ask in the endgame (only expiring food is cut).
    assert final.ask == normal.ask
    assert final.ask >= final.accept_at


@pytest.mark.parametrize("grade", ["A", "B", "C"])
@pytest.mark.parametrize("hours_left", [24, 23, 12, 3, 0, -1])
def test_goods_sale_never_destroys_book_at_the_end(grade: str, hours_left: int) -> None:
    now = ROUND_END - hours_left * HOUR
    policy = Policy().seller
    item = Unit.model_validate(unit(CURTAIN, 1, grade=grade, now=now))
    priced = quote(item, now, None, policy)
    assert priced.ask >= priced.accept_at >= priced.floor >= item.cost
    # Book includes the unsold unit at cost; sale exchanges that asset for cash.
    assert priced.accept_at - item.cost >= round(item.cost * policy.min_margin) - 1


@pytest.mark.parametrize("expiry_offset", [-1, 0, 1, None])
def test_only_food_expiring_by_the_round_end_can_liquidate_below_cost(
    expiry_offset: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = ROUND_END - 2 * HOUR
    policy = Policy().seller.model_copy(update={"clearance_shelf_ratio": 0.0})
    row = unit(MILK, 1, now=now)
    row["expires_at"] = (
        ROUND_END + expiry_offset * HOUR if expiry_offset is not None else None
    )
    item = Unit.model_validate(row)
    priced = quote(item, now, 48, policy)
    if expiry_offset is not None and expiry_offset <= 0:
        assert priced.floor == priced.accept_at == round(item.cost * 0.5)
    else:
        assert priced.floor == round(item.cost * (1 + policy.min_margin))
        monkeypatch.setattr("r3.pricing.sell_pressure", lambda _now: 0.0)
        normal = quote(item, now, 48, policy)
        assert priced.accept_at == normal.accept_at


def test_restock_buys_nothing_in_the_last_quarter_hour(tmp_path: Path) -> None:
    for name, stamp, expect_buy in (
        ("early", ROUND_END - 100 * HOUR, True),
        ("day_before", ROUND_END - 30 * HOUR, True),
        ("late", ROUND_END - 0.2 * HOUR, False),  # 11/17: stop at 0.25 h
    ):
        folder = write_market(
            tmp_path / name,
            [unit(LAMP, 1, now=stamp)],
            balance=20_000_000,
        )
        lines, _ = plan(
            read_inventory(folder),
            read_catalog(folder),
            read_demand(folder),
            RestockPolicy(),
            RestockState(first_run_at=stamp - 48 * HOUR),
            stamp,
        )
        assert bool(lines) is expect_buy, name
