"""Round 3 endgame: taper buying and price against the final Book value.

Book includes unsold inventory at cost. Goods and food lasting beyond the
closing clock keep their normal cost floor and acceptance margin, including
grade-specific refund protection. Only food expiring by that clock can use the
below-cost liquidation floor.
The buying and selling clocks are unchanged. Pure functions of ``now``; tests
pass their own clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

HOUR: Final = 3600.0
# Round 3 ends 11/17 23:59 KST; the leaderboard at that moment is the grade.
ROUND_END: Final = datetime(
    2026, 11, 17, 23, 59, tzinfo=timezone(timedelta(hours=9))
).timestamp()
# Stock budget: full until 12 h before the end, zero from 2 h before (72/36 until
# 11/14 19:1x). Book is cash plus stock at cost (it matches the board's profit +
# stock_cost), so holding stock at the close costs nothing, while the old taper cut
# restock room from 285M (11/14 18:52: cash 482M, stock 504M, stock_share 0.8) to
# 134M by 11/15 06:52, 2M by 12:52 and 0 from 18:52: about 59 h with no restock of
# even the best sellers. Over-buying is held back by the 12 h sales caps, the
# stuck rule and max_units, not by this clock.
# 11/17 05:5x (owner): 12 h / 2 h -> 1 h / 0.25 h. Book is profit + stock cost, so
# buying keeps the book; the old taper left no buying room from about 17:30.
BUY_TAPER_START_HOURS: Final = 1.0
BUY_STOP_HOURS: Final = 0.25
# Selling: normal until 24 h before the end, full liquidation from 3 h before.
SELL_PRESSURE_START_HOURS: Final = 24.0
SELL_PRESSURE_FULL_HOURS: Final = 3.0
# Only food expiring by ROUND_END may fall to this share of cost. Other units
# keep their normal floor. Opening asks fall by this share of list, floor-bound.
LIQUIDATION_FLOOR_OF_COST: Final = 0.5
LIQUIDATION_ASK_CUT: Final = 0.25


def _ramp(hours_left: float, start: float, full: float) -> float:
    """0 at ``start`` hours left or more, 1 at ``full`` or fewer, linear between."""
    if hours_left >= start:
        return 0.0
    if hours_left <= full:
        return 1.0
    return (start - hours_left) / (start - full)


def buy_share(now: float, end: float = ROUND_END) -> float:
    """Share of the normal stock budget still allowed (1 -> 0 before the end)."""
    hours_left = (end - now) / HOUR
    return 1.0 - _ramp(hours_left, BUY_TAPER_START_HOURS, BUY_STOP_HOURS)


def sell_pressure(now: float, end: float = ROUND_END) -> float:
    """How far prices have moved toward liquidation (0 normal, 1 full)."""
    return _ramp(
        (end - now) / HOUR, SELL_PRESSURE_START_HOURS, SELL_PRESSURE_FULL_HOURS
    )
