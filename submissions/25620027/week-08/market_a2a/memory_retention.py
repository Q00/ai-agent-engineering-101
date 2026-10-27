"""Bound disposable decision memory without touching receipt or settlement logs."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from market_a2a.memory_models import Trade

MAX_SHOPS: Final = 64
MAX_PER_SHOP: Final = 200
MAX_RECORDS: Final = 2048
MAX_RECORD_BYTES: Final = 8192
MAX_FILE_BYTES: Final = 2 * 1024 * 1024
MAX_LOAD_BYTES: Final = 16 * 1024 * 1024
MAX_NAME_BYTES: Final = 256
TTL: Final = timedelta(days=14)
LEGACY_TIME: Final = datetime(1970, 1, 1, tzinfo=UTC)


def retained(
    shops: dict[str, tuple[Trade, ...]], now: datetime
) -> dict[str, tuple[Trade, ...]]:
    """Keep newest bounded observations; unknown legacy dates are not invented."""
    entries = sorted(
        (
            (trade.observed_at or LEGACY_TIME, ordinal, name, trade)
            for name, trades in shops.items()
            for ordinal, trade in enumerate(trades)
            if len(name.encode()) <= MAX_NAME_BYTES
            and (trade.observed_at is None or now - TTL <= trade.observed_at <= now)
        ),
        key=lambda entry: (entry[0], entry[1]),
        reverse=True,
    )
    counts: Counter[str] = Counter()
    kept: dict[str, list[Trade]] = {}
    # Reserve space for names, commas and JSON object delimiters.
    budget = MAX_FILE_BYTES - 32768
    total = 0
    for _, _, name, trade in entries:
        size = len(trade.model_dump_json().encode())
        if (
            counts[name] >= MAX_PER_SHOP
            or (name not in kept and len(kept) >= MAX_SHOPS)
            or size > MAX_RECORD_BYTES
            or size > budget
        ):
            continue
        kept.setdefault(name, []).append(trade)
        counts[name] += 1
        total += 1
        budget -= size + 1
        if total == MAX_RECORDS:
            break
    return {name: tuple(reversed(trades)) for name, trades in kept.items()}
