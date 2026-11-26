"""11/17 10:52 (owner): market-first at 1.05, today's A accept line, shop rest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.buyer import LOSING_SHOP_MINUTES, LOSING_SHOP_STREAK
from r3.events import EventLedger
from r3.market_files import Unit
from r3.restock import MARKET_FIRST_MARGIN
from r3.seller import INEXTRO_SINCE, Quote, mf_quote
from tests.r3_stock import LAMP, unit

if TYPE_CHECKING:
    from pathlib import Path


def lamp(grade: str = "A", cost: int = 100_000, *, today: bool = True) -> Unit:
    stamp = INEXTRO_SINCE + 7200 if today else INEXTRO_SINCE - 7200
    return Unit.model_validate(unit(LAMP, 1, grade=grade, cost=cost, now=stamp))


def test_todays_a_accept_line_follows_the_market_median() -> None:
    assert MARKET_FIRST_MARGIN == 1.05
    priced = Quote(140_000, 110_000, 108_000, "A", clearance=False, stale=False)
    # Median 107k: max(105k, min(104,860, 115k)) = 105k (lowered from 110k).
    low = mf_quote(lamp(), priced, 107_000.0)
    assert (low.ask, low.accept_at, low.floor) == (140_000, 105_000, 105_000)
    # Median 200k: capped at cost x 1.15.
    assert mf_quote(lamp(), priced, 200_000.0).accept_at == 115_000
    # Median 112k: 109,760.
    assert mf_quote(lamp(), priced, 112_000.0).accept_at == 109_760
    # B, older units, no median: untouched.
    b = Quote(140_000, 110_000, 108_000, "B", clearance=False, stale=False)
    assert mf_quote(lamp("B"), b, 107_000.0) is b
    assert mf_quote(lamp(today=False), priced, 107_000.0) is priced
    assert mf_quote(lamp(), priced, None) is priced


def attempt(events: EventLedger, order: str, shop: str, outcome: str) -> None:
    events.record_attempt(
        order=order,
        shop=shop,
        shop_name="s",
        sku=f"G{order}",
        list_price=10_000,
        price=None,
        outcome=outcome,  # type: ignore[arg-type]
        reason="",
    )


def test_a_shop_rests_after_twenty_straight_failures(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    assert (LOSING_SHOP_STREAK, LOSING_SHOP_MINUTES) == (20, 60.0)
    for i in range(19):
        attempt(events, str(i), "https://gom.test/", "no_deal")
    assert events.losing_shops(LOSING_SHOP_STREAK, LOSING_SHOP_MINUTES) == frozenset()
    attempt(events, "19", "https://gom.test/", "no_deal")
    attempt(events, "x", "https://ok.test/", "no_deal")
    rested = events.losing_shops(LOSING_SHOP_STREAK, LOSING_SHOP_MINUTES)
    assert rested == frozenset({"https://gom.test"})
    attempt(events, "20", "https://gom.test/", "agreed")
    assert events.losing_shops(LOSING_SHOP_STREAK, LOSING_SHOP_MINUTES) == frozenset()


def test_shop_rest_is_off_after_the_13_08_judgement() -> None:
    from r3.buyer import LOSING_SHOP_ON  # noqa: PLC0415 - local to this test

    assert LOSING_SHOP_ON is False
