"""Usage uncertainty disables paid calls; long-context pricing is accounted for."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from market_a2a.anthropic_fallback import HAIKU_MODEL
from r3.events import EventLedger
from r3.model_client import ModelClient, price

if TYPE_CHECKING:
    from pathlib import Path


def test_haiku_price_uses_the_long_context_rate_only_over_the_threshold() -> None:
    assert price(HAIKU_MODEL, 100_000, 1000) == pytest.approx(0.0105)
    expected = (100_001 * 0.50 + 1000 * 2.50) / 1_000_000
    assert price(HAIKU_MODEL, 100_001, 1000) == pytest.approx(expected, abs=1e-6)


def test_environment_client_never_uses_paid_calls_without_the_usage_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-placeholder")
    monkeypatch.setenv("AX_R3_TRADE_MODEL", "1")
    assert ModelClient.from_env(None, gate="AX_R3_TRADE_MODEL") is None


def test_unreadable_usage_or_cooldown_blocks_calls_instead_of_assuming_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = EventLedger(tmp_path / "events.db")
    client = ModelClient("offline", events=events, quota=None)

    def unavailable(*_args: object) -> None:
        message = "unavailable"
        raise OSError(message)

    monkeypatch.setattr(events, "spend", unavailable)
    assert client.pick_model(HAIKU_MODEL, "trade") is None
    monkeypatch.setattr(events, "get_value", unavailable)
    assert client.pick_model(HAIKU_MODEL, "trade") is None
