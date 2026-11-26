"""Demand-backed stock targets; the restock hook owns cash and quantity limits."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from r3.growth import GrowthEvidence, SkuGrowth
    from r3.market_files import CatalogItem
    from r3.policy import RestockPolicy

MIN_MARKET_SETTLES: Final = 5
BAND_OVERLAP: Final = 0.2
MIN_EXPOSURE_HOURS: Final = 0.25
# Acquisition forecast assumption: published grade mix with cost-range midpoints,
# 0.7 * 0.65 + 0.2 * 0.50 + 0.1 * 0.35 = 0.59 of list before current surge.
# This is an estimate; actual retained receipts and the historical cost basis
# conservatively cap expansion. Cash affordability still uses the 0.75 worst case.
EXPECTED_BASE_COST_SHARE: Final = 0.59


@dataclass(frozen=True, slots=True)
class StockTarget:
    """Desired total stock, including units already held, for one SKU."""

    item: CatalogItem
    total: int


def _mature(growth: SkuGrowth, policy: RestockPolicy) -> bool:
    """Require a complete, sufficiently large cost cohort before reinvesting."""
    return (
        growth.mature_sales >= policy.min_mature_sales
        and growth.mature_net is not None
        and growth.mature_cost is not None
        and growth.mature_cost > 0
    )


def _losing(growth: SkuGrowth | None, policy: RestockPolicy) -> bool:
    return (
        growth is not None
        and _mature(growth, policy)
        and growth.mature_net is not None
        and growth.mature_net <= 0
    )


def proven_targets(  # noqa: PLR0913 - independent planning inputs
    fitting: Sequence[CatalogItem],
    policy: RestockPolicy,
    evidence: GrowthEvidence,
    market_targets: Mapping[str, int],
    held: Mapping[str, int],
    *,
    surge: Mapping[str, float] | None = None,
) -> list[StockTarget]:
    """Unify own and market depth before spending any line or cash allowance.

    Actual recent sellers precede market-only targets. Among them, replenish the
    least covered first, then prefer forecast return per cost-hour and item value.
    Below 15 minutes of observed exposure, unadjusted one-hour sales are a lower
    bound; a brief listing cannot extrapolate one sale into a large new lot.
    """
    if not evidence.available:
        return []
    factors = surge or {}
    ranked: list[tuple[tuple[int, float, float, int], StockTarget]] = []
    for item in fitting:
        row = evidence.by_sku.get(item.sku)
        if row is None or not _mature(row, policy) or _losing(row, policy):
            continue
        cost, net = row.mature_cost, row.mature_net
        if cost is None or net is None:
            continue
        retained_receipts = (cost + net) / row.mature_sales
        next_cost = max(
            cost / row.mature_sales,
            EXPECTED_BASE_COST_SHARE * item.list_price * factors.get(item.sku, 1.0),
        )
        # Historical cost may already include a surge. Compare it with the new
        # estimate rather than multiplying the historical basis by surge again.
        if next_cost >= retained_receipts:
            continue
        rate = row.sales_1h
        if MIN_EXPOSURE_HOURS <= row.in_stock_hours <= 1:
            rate /= row.in_stock_hours
        own_target = (
            max(policy.replenish_qty, math.ceil(rate * policy.cover_hours))
            if row.sales_1h > 0
            else 0
        )
        target = min(
            policy.units_per_sku,
            max(market_targets.get(item.sku, 0), own_target),
        )
        held_qty = held.get(item.sku, 0)
        if target <= held_qty:
            continue
        roi_hour = 0.0
        if row.median_hold_hours is not None and row.median_hold_hours > 0:
            roi_hour = (
                (retained_receipts - next_cost)
                / next_cost
                / max(MIN_EXPOSURE_HOURS, row.median_hold_hours)
            )
        priority = (
            0 if row.sales_1h > 0 else 1,
            held_qty / target,
            -roi_hour,
            -item.list_price,
        )
        ranked.append((priority, StockTarget(item, target)))
    ranked.sort(key=lambda pair: pair[0])
    return [target for _, target in ranked]


def _covered(price: int, held_prices: Sequence[int]) -> bool:
    """Within 20% of an existing price is one coverage band, not a new probe."""
    return any(abs(price - held) <= BAND_OVERLAP * max(held, 1) for held in held_prices)


def probe_targets(  # noqa: PLR0913 - independent planning inputs
    fitting: Sequence[CatalogItem],
    policy: RestockPolicy,
    evidence: GrowthEvidence,
    market_counts: Mapping[str, int] | None,
    held: Mapping[str, int],
    *,
    categories: frozenset[str],
) -> list[StockTarget]:
    """Cover missing price points with small, market-supported experiments.

    Greedily cover the most settled demand not within 20% of a held/planned price,
    up to ``price_bands`` total points per category. Counts come only from actual
    settlements; demand.json supplies category names, never inferred weights.
    With neither ledger evidence nor market counts, a bounded bootstrap is the
    only exception: one small lot per uncovered category, no depth targets.
    """
    bootstrap = not evidence.available and market_counts is None
    counts = market_counts or {}
    pools: dict[str, list[CatalogItem]] = {}
    prices: dict[str, list[int]] = {}
    for item in sorted(fitting, key=lambda item: item.list_price):
        if held.get(item.sku, 0) and not _covered(
            item.list_price, prices.get(item.category, [])
        ):
            prices.setdefault(item.category, []).append(item.list_price)
        if (
            item.category in categories
            and not held.get(item.sku, 0)
            and not _losing(evidence.by_sku.get(item.sku), policy)
            and (bootstrap or counts.get(item.sku, 0) >= MIN_MARKET_SETTLES)
        ):
            pools.setdefault(item.category, []).append(item)
    targets: list[StockTarget] = []
    limit = 1 if bootstrap else policy.price_bands
    while pools:
        candidates: list[tuple[float, CatalogItem]] = []
        for category, items in pools.items():
            covered = prices.get(category, [])
            if len(covered) >= limit:
                continue
            for item in items:
                if _covered(item.list_price, covered):
                    continue
                weight = sum(
                    counts.get(other.sku, 1 if bootstrap else 0)
                    for other in items
                    if not _covered(other.list_price, covered)
                    and _covered(other.list_price, [item.list_price])
                )
                candidates.append((weight * item.list_price, item))
        if not candidates:
            break
        _, chosen = max(candidates, key=lambda pair: (pair[0], pair[1].sku))
        targets.append(StockTarget(chosen, min(policy.probe_qty, policy.units_per_sku)))
        prices.setdefault(chosen.category, []).append(chosen.list_price)
        pools[chosen.category].remove(chosen)
    return targets
