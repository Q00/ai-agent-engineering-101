"""Read a bounded cached report without repeating ledger work per model call."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from pydantic import ValidationError

from r3.buyer_report_models import BuyerReport

if TYPE_CHECKING:
    from pathlib import Path

REPORT_FILE: Final = "r3-buyer-report.json"
REPORT_EVERY: Final = 1800.0
MAX_REPORT_AGE: Final = 3600.0
SUMMARY_LIMIT: Final = 2400


class BuyerReportView(TypedDict):
    """Facts and freshness shown to slow-loop models, never an action directive."""

    status: Literal["unavailable", "stale", "current"]
    age_seconds: float | None
    summary: str


def cached_buyer_report(path: Path, *, now: float | None = None) -> BuyerReportView:
    """Keep missing/stale observations explicit instead of inventing zero results."""
    try:
        report = BuyerReport.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return {"status": "unavailable", "age_seconds": None, "summary": ""}
    stamp = time.time() if now is None else now
    age = max(0.0, stamp - report.generated_at)
    fresh = report.ledger_fresh and age <= MAX_REPORT_AGE
    return {
        "status": "current" if fresh else "stale",
        "age_seconds": round(age, 3),
        "summary": report.summary[:SUMMARY_LIMIT],
    }
