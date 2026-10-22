"""Deterministic hourly opening asks; the clock is read by Python, not the LLM."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pricing_simulator import candidate_price


KST = ZoneInfo("Asia/Seoul")
SALE_MINUTES = 20
CARD_NOTICE = (
    "Hourly opening-ask sale: every hour from :00 through :19 KST, "
    "the first ask is 1.06 times the item reserve (rounded up); "
    "at other times it is 1.09 times reserve. Final prices remain negotiable."
)


def choose_hourly_price(reserve: int, at: datetime | None = None) -> tuple[str, int, str]:
    """Choose once at negotiation start; return cohort, ask, and KST timestamp."""
    at = at or datetime.now(KST)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a timezone-aware timestamp is required")
    local = at.astimezone(KST)
    sale = local.minute < SALE_MINUTES
    arm = "hourly_sale_1.06" if sale else "hourly_regular_1.09"
    ask = candidate_price(reserve, "Low" if sale else "Mid")
    return arm, ask, local.isoformat(timespec="seconds")
