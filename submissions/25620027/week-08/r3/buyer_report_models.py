"""Typed boundaries for the buyer's local, model-free performance report."""

from __future__ import annotations

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

HOUR: Final = 3600
MATURITY: Final = 6 * HOUR + 180
MAX_LAG: Final = 180
BANDS: Final = (
    "<50000",
    "50000-199999",
    "200000-499999",
    "500000-999999",
    ">=1000000",
    "unknown",
)


class ReportPayload(BaseModel):
    """Only ledger fields used in this report cross the JSON boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    order: str = ""
    buyer: str = ""
    unit: str = ""
    item: str = ""
    sku: str = ""
    amount: int | None = None
    value: int | None = None
    refunded: int | bool | None = None
    budget: int | None = None
    expires: float | None = None


class BudgetMetrics(BaseModel):
    """An accumulator is mutable while streaming the bounded order cohort."""

    orders: int = 0
    settled: int = 0
    settled_rate: float | None = None
    failed: int = 0
    pending_or_unknown: int = 0
    no_stock_orders: int = 0
    no_stock_rate: float | None = None
    shop_timeouts: int = 0
    retries: int = 0
    retry_settled: int = 0
    provisional_surplus_known_subtotal: int = 0
    provisional_surplus: int | None = None
    provisional_known_orders: int = 0
    provisional_surplus_per_order: float | None = None
    mature_surplus_known_subtotal: int = 0
    mature_surplus: int | None = None
    mature_known_orders: int = 0
    mature_surplus_per_order: float | None = None
    immature_or_unknown_orders: int = 0
    duplicate_settlements: int = 0
    unidentified_orders: int = 0


class BuyerReport(BaseModel):
    """Completed-hour order cohort, with explicit freshness and missing evidence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    generated_at: float
    cohort_start: float
    cohort_end: float
    ledger_latest_at: float | None
    ledger_fresh: bool
    authoritative_order_times: int
    local_observation_times: int
    bands: dict[str, BudgetMetrics] = Field(default_factory=dict)
    mature_bands: dict[str, BudgetMetrics]
    mature_cohort_start: float
    mature_cohort_end: float
    summary: str
    notes: tuple[str, ...]


def budget_band(budget: int | None) -> str:
    """Keep unavailable historical budgets outside all known budget bands."""
    if budget is None or budget <= 0:
        return "unknown"
    for ceiling, name in zip((50000, 200000, 500000, 1000000), BANDS, strict=False):
        if budget < ceiling:
            return name
    return ">=1000000"
