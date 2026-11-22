"""Shared test setup: whole-order buyer runs never explore unless a test asks."""

from __future__ import annotations

import random
import time
from datetime import datetime

import pytest

from r3 import buyer, pricing, restock


class _NeverExplore(random.Random):
    def random(self) -> float:
        return 1.0


@pytest.fixture(autouse=True)
def _no_buyer_exploration(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default draw comes from the module; tests that pass ``rng`` still explore.
    monkeypatch.setattr(buyer, "random", _NeverExplore())


@pytest.fixture(autouse=True)
def _new_goods_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # Restock tests predate the 11/16 09:2x new-goods pause; a test that checks the
    # pause sets it back on itself.
    monkeypatch.setattr(restock, "NEW_GOODS_PAUSED", False)


@pytest.fixture
def r3_restock_now() -> float:
    """A regular supply day before the Round 3 endgame taper."""
    return datetime.fromisoformat("2026-11-13T12:00:00+09:00").timestamp()


@pytest.fixture(autouse=True)
def _no_endgame_at_the_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # From 11/16 23:59 (24 h before the close) the real clock is inside the endgame
    # ramp, so quotes priced at time.time() would carry sell pressure. Tests that
    # price at "now" keep normal prices; explicit endgame times keep the real ramp.
    real = pricing.sell_pressure

    def pressure(now: float) -> float:
        # 11/17 21:1x: 3600 -> 120 s. Fixed test times (ROUND_END - 2 h) fell inside the
        # hour as the real clock neared the close; "now" quotes run within seconds.
        return 0.0 if abs(now - time.time()) < 120 else real(now)

    monkeypatch.setattr(pricing, "sell_pressure", pressure)


@pytest.fixture(autouse=True)
def _clear_price_walls() -> object:
    """Each test starts with no remembered price walls (r3/buyer.py, 11/17 17:1x)."""
    buyer._PRICE_WALLS.clear()  # noqa: SLF001 - test isolation
    yield
    buyer._PRICE_WALLS.clear()  # noqa: SLF001
