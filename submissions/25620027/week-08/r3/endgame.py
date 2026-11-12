"""Round 3 endgame: turn stock into cash before the final leaderboard.

The board counts shop net profit as balance - start - injections, so stock still
on the shelf at the round's end scores zero. Two clocks run off the round end:
the stock budget tapers to nothing (no new buying), then the asks and the accept
line slide toward a liquidation floor below cost, since any sale beats a unit
that scores zero. Pure functions of ``now``; tests pass their own clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

HOUR: Final = 3600.0
# Round 3 ends 11/17 23:59 KST; the leaderboard at that moment is the grade.
ROUND_END: Final = datetime(
    2026, 11, 17, 23, 59, tzinfo=timezone(timedelta(hours=9))
).timestamp()
# Stock budget: full until 72 h before the end, zero from 36 h before.
BUY_TAPER_START_HOURS: Final = 72.0
BUY_STOP_HOURS: Final = 36.0
# Selling: normal until 24 h before the end, full liquidation from 3 h before.
SELL_PRESSURE_START_HOURS: Final = 24.0
SELL_PRESSURE_FULL_HOURS: Final = 3.0
# At full pressure the accept line falls to this share of cost and the opening
# ask drops by this share of list (a grade A ask of 95% opens at 70%).
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
