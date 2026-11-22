"""Exact order settlement and maturation, independent of report serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from r3.buyer_report_models import MATURITY, BudgetMetrics

if TYPE_CHECKING:
    import sqlite3

    from r3.market_files import CatalogItem


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Read-only sources and one fixed report observation time."""

    facts: sqlite3.Connection
    events: sqlite3.Connection
    catalog: dict[str, CatalogItem]
    now: float
    fresh: bool


_SETTLED: Final = TypeAdapter(
    tuple[float, str, int | None, int | None, float | None, int | None, int | None, str]
    | None
)
_COUNT: Final = TypeAdapter(tuple[int])
_PAYMENT: Final = TypeAdapter(tuple[int] | None)


def order_result(  # noqa: PLR0913 - explicit per-order evidence inputs
    context: ReportContext,
    metric: BudgetMetrics,
    order_id: str,
    kind: str | None,
    *,
    expires: float | None,
    retry: bool,
) -> None:
    """Actual settlement wins over local attempts; unknowns never become zero."""
    facts, events = context.facts, context.events
    fresh, now, catalog = context.fresh, context.now, context.catalog
    count = _COUNT.validate_python(
        facts.execute(
            "SELECT count(*) FROM settlements WHERE order_id=?",
            (order_id,),
        ).fetchone()
    )[0]
    metric.duplicate_settlements += max(0, count - 1)
    sale = _SETTLED.validate_python(
        facts.execute(
            """SELECT
s.ts,s.sku,s.amount,s.value,b.ts,b.refund,b.refund_full,s.unit FROM
settlements s LEFT JOIN breaks b ON b.unit=s.unit WHERE s.order_id=?
ORDER BY s.seq LIMIT 1""",
            (order_id,),
        ).fetchone()
    )
    if sale is None:
        payment = _PAYMENT.validate_python(
            events.execute(
                "SELECT paid FROM payments WHERE order_id=?",
                (order_id,),
            ).fetchone()
        )
        failed = (
            fresh
            and payment is not None
            and payment[0] == 0
            and expires is not None
            and now >= expires + 180
        )
        if failed:
            metric.failed += 1
            metric.provisional_known_orders += 1
            metric.mature_known_orders += 1
        else:
            metric.pending_or_unknown += 1
            metric.immature_or_unknown_orders += 1
        return
    metric.settled += 1
    metric.retry_settled += int(retry)
    at, sku, amount, value, broken_at, refund, refund_full, unit = sale
    item = catalog.get(sku)
    goods = kind != "food" and (item is None or item.kind != "food")
    if value is None and goods and item is not None and item.list_price > 0:
        value = item.list_price
    if broken_at is not None:
        value = 0
        refund = amount if refund_full else refund
    else:
        refund = 0
    known = (
        amount is not None and value is not None and refund is not None and count == 1
    )
    if known and amount is not None and value is not None and refund is not None:
        surplus = value - amount + refund
        metric.provisional_surplus_known_subtotal += surplus
        metric.provisional_known_orders += 1
        if fresh and (not goods or (bool(unit) and now - at >= MATURITY)):
            metric.mature_surplus_known_subtotal += surplus
            metric.mature_known_orders += 1
            return
    metric.immature_or_unknown_orders += 1
