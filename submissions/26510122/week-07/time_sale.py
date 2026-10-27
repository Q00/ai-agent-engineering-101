"""A dated opening-price campaign; Python checks the clock, not the LLM."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pricing_simulator import candidate_price


KST = ZoneInfo("Asia/Seoul")
SALE_START_KST = datetime(2026, 10, 22, 0, 0, tzinfo=KST)
SALE_END_KST = datetime(2026, 10, 29, 0, 0, tzinfo=KST)
CARD_NOTICE = (
    "Time Sale Now! Limited-time offers through Oct 28, 2026 23:59 KST. "
    "Final prices remain negotiable."
)


def choose_campaign_price(reserve: int, at: datetime | None = None) -> tuple[str, int, str]:
    """Choose once at negotiation start; return cohort, ask, and KST timestamp."""
    at = at or datetime.now(KST)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a timezone-aware timestamp is required")
    local = at.astimezone(KST)
    sale = SALE_START_KST <= local < SALE_END_KST
    arm = "campaign_sale_1.09" if sale else "campaign_regular_1.12"
    ask = candidate_price(reserve, "Mid" if sale else "High")
    return arm, ask, local.isoformat(timespec="seconds")
