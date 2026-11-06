"""Round 3 endgame: stop buying, then sell stock down before the final board."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.endgame import HOUR, ROUND_END, buy_share, sell_pressure
from r3.market_files import Unit
from r3.policy import Policy, RestockPolicy
from r3.pricing import quote
from r3.restock import RestockState, plan, read_catalog, read_demand, read_inventory
from tests.r3_stock import CURTAIN, LAMP, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path


def test_buying_tapers_out_and_prices_slide_only_near_the_end() -> None:
    assert buy_share(ROUND_END - 100 * HOUR) == 1.0
    assert buy_share(ROUND_END - 54 * HOUR) == 0.5
    assert buy_share(ROUND_END - 36 * HOUR) == 0.0
    assert sell_pressure(ROUND_END - 30 * HOUR) == 0.0
    assert 0.0 < sell_pressure(ROUND_END - 12 * HOUR) < 1.0
    assert sell_pressure(ROUND_END - 1 * HOUR) == 1.0
    assert sell_pressure(ROUND_END + HOUR) == 1.0


def test_endgame_quote_sells_below_cost_but_never_under_half_of_it() -> None:
    policy = Policy().seller
    early = ROUND_END - 48 * HOUR
    late = ROUND_END - 2 * HOUR
    mattress = Unit.model_validate(unit(CURTAIN, 1, grade="A", now=late))
    normal = quote(mattress, early, None, policy)
    final = quote(mattress, late, None, policy)
    assert normal.endgame == 0.0
    assert normal.accept_at > mattress.cost
    assert final.endgame == 1.0
    assert final.accept_at == final.floor == round(mattress.cost * 0.5)
    assert final.ask == round(normal.ask - 236900 * 0.25)
    assert final.ask >= final.accept_at


def test_restock_buys_nothing_in_the_last_36_hours(tmp_path: Path) -> None:
    for name, stamp, expect_buy in (
        ("early", ROUND_END - 100 * HOUR, True),
        ("late", ROUND_END - 30 * HOUR, False),
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
