"""Read-only, exact-unit evidence for bounded inventory growth.

Net is a mature sold-unit contribution after refunds and pre-sale storage, not
whole-shop profit. Unknown unit economics never become a zero-cost success.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, Final

from r3.accounting import DAY
from r3.growth_db import HOUR, GrowthFacts, GrowthSale, GrowthStock, read_growth
from r3.ledger_db import BREAK_WINDOW

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from r3.market_files import CatalogItem, Inventory

__all__ = [
    "GrowthEvidence",
    "GrowthFacts",
    "GrowthSale",
    "GrowthStock",
    "SkuGrowth",
    "build_growth",
    "summarize_growth",
]

MIN_RISK_SALES: Final = 20


@dataclass(frozen=True, slots=True)
class SkuGrowth:
    """Observed demand and mature contribution for one SKU."""

    sales_1h: int = 0
    in_stock_hours: float = 0
    mature_sales: int = 0
    mature_net: int | None = None
    mature_cost: int | None = None
    median_hold_hours: float | None = None


@dataclass(frozen=True, slots=True)
class GrowthEvidence:
    """Unavailable evidence must not authorize aggressive growth."""

    by_sku: Mapping[str, SkuGrowth]
    refund_reserve: int = 0
    available: bool = True


def _exposure(intervals: Sequence[tuple[float, float]], now: float) -> float:
    """Union clipped intervals; simultaneous units do not multiply availability."""
    end, total = now - HOUR, 0.0
    for left, right in sorted(intervals):
        start, stop = max(left, now - HOUR, end), min(right, now)
        if stop > start:
            total += stop - start
            end = stop
    return total / HOUR


def _reserve(sales: Sequence[GrowthSale], now: float) -> int:
    """Remaining risk from mature grade-specific break times, with a buffer.

    Two pseudo observations keep a finite sample with zero remaining breaks from
    claiming certainty. Sparse/unknown grades or terms reserve the full obligation.
    """
    cohorts: dict[str, list[float]] = defaultdict(list)
    for sale in sales:
        if sale.goods and sale.unit and now - sale.at >= BREAK_WINDOW:
            cohorts[sale.grade].append(
                math.inf if sale.broken_at is None else sale.broken_at - sale.at
            )
    mean = variance = outstanding = 0.0
    for sale in sales:
        age = now - sale.at
        if (
            sale.own
            and sale.goods
            and sale.broken_at is not None
            and sale.refund is None
        ):
            mean += sale.amount or 0
            outstanding += sale.amount or 0
            continue
        if (
            not sale.own
            or not sale.goods
            or sale.broken_at is not None
            or age >= BREAK_WINDOW
        ):
            continue
        horizon = min(BREAK_WINDOW, (sale.warranty_hours or 0) * HOUR)
        if sale.warranty_hours is not None and age >= horizon:
            continue
        if sale.amount is None:
            continue  # Adapter marks missing own amounts unavailable.
        cohort = cohorts[sale.grade]
        p = 1.0
        if sale.warranty_hours is not None and len(cohort) >= MIN_RISK_SALES:
            survivors = sum(delay > age for delay in cohort)
            remaining = sum(age < delay <= horizon for delay in cohort)
            p = (remaining + 1) / (survivors + 2)
        outstanding += sale.amount
        mean += sale.amount * p
        variance += sale.amount**2 * p * (1 - p)
    return math.ceil(min(outstanding, mean + 2 * math.sqrt(variance)))


def summarize_growth(facts: GrowthFacts) -> GrowthEvidence:  # noqa: C901 - exact-unit completeness and interval accounting
    """Compute SKU contributions and availability without I/O or inferred sales."""
    own: dict[str, list[GrowthSale]] = defaultdict(list)
    intervals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    seen: set[str] = set()
    for sale in facts.sales:
        if sale.unit and sale.unit in seen:
            return GrowthEvidence({}, available=False)
        seen.add(sale.unit)
        if not sale.own:
            continue
        if sale.amount is None:
            return GrowthEvidence({}, available=False)
        own[sale.sku].append(sale)
        stock = facts.stock.get(sale.unit)
        if stock is not None:
            intervals[sale.sku].append((max(stock.supplied, stock.first_seen), sale.at))
    for unit, sku in facts.current.items():
        stock = facts.stock.get(unit)
        if stock is not None and unit not in seen:
            intervals[sku].append((max(stock.supplied, stock.first_seen), facts.now))
    result: dict[str, SkuGrowth] = {}
    for sku in own.keys() | intervals.keys():
        sales = own[sku]
        mature = [s for s in sales if facts.now - s.at >= BREAK_WINDOW]
        cost, net, known = 0, 0.0, bool(mature)
        holds: list[float] = []
        for sale in mature:
            stock = facts.stock.get(sale.unit)
            if (
                stock is None
                or stock.supplied > sale.at
                or (sale.broken_at is not None and sale.refund is None)
            ):
                known = False
                continue
            hold = sale.at - stock.supplied
            cost += stock.cost
            net += (sale.amount or 0) - stock.cost - (sale.refund or 0)
            net -= stock.cost * facts.storage_rate * hold / DAY if sale.goods else 0
            holds.append(hold / HOUR)
        result[sku] = SkuGrowth(
            sales_1h=sum(facts.now - HOUR <= s.at <= facts.now for s in sales),
            in_stock_hours=_exposure(intervals[sku], facts.now),
            mature_sales=len(mature),
            mature_net=math.floor(net) if known else None,
            mature_cost=cost if known else None,
            median_hold_hours=median(holds) if known else None,
        )
    return GrowthEvidence(result, _reserve(facts.sales, facts.now))


def build_growth(
    run_dir: Path,
    shop_url: str,
    catalog: dict[str, CatalogItem],
    inventory: Inventory,
    now: float,
) -> GrowthEvidence:
    """Load evidence through the read-only adapter, preserving the public API."""
    facts = read_growth(run_dir, shop_url, catalog, inventory, now)
    return (
        GrowthEvidence({}, available=False)
        if facts is None
        else summarize_growth(facts)
    )
