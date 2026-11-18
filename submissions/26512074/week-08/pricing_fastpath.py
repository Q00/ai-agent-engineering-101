"""Deterministic seller counteroffers to avoid per-message LLM calls."""
from __future__ import annotations

import math


def counter_ask(floor: int | float, opening_ask: int | float, previous_ask: int | float | None) -> int:
    """Open at the observed price, then concede toward the non-negotiable floor.

    This module never approves a sale; the seller still checks the buyer's
    real offer against the price floor before issuing a signed receipt.
    """
    for value in (floor, opening_ask, previous_ask):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("quote must be a finite numeric value")
    minimum = math.ceil(floor)
    start = max(minimum, math.ceil(opening_ask))
    if previous_ask is None:
        return start
    previous = max(minimum, math.ceil(previous_ask))
    concession = max(1, math.ceil((start - minimum) / 4))
    return max(minimum, min(start, previous - concession))
