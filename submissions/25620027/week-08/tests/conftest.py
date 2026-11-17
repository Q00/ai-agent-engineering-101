"""Shared test setup: whole-order buyer runs never explore unless a test asks."""

from __future__ import annotations

import random

import pytest

from r3 import buyer


class _NeverExplore(random.Random):
    def random(self) -> float:
        return 1.0


@pytest.fixture(autouse=True)
def _no_buyer_exploration(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default draw comes from the module; tests that pass ``rng`` still explore.
    monkeypatch.setattr(buyer, "random", _NeverExplore())
