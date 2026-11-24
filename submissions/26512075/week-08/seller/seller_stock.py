"""Offline stock selection helpers for a Round 3 seller."""
from __future__ import annotations
import math

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

from market_inventory import InventoryError, InventoryUnit, available_units, parse_inventory, read_inventory


@dataclass(frozen=True)
class StockChoice:
    unit: InventoryUnit
    reserve: int  # Minimum integer price under this cost-only policy.


def _round3_units(market_dir: Path) -> list[InventoryUnit]:
    data = read_inventory(market_dir)
    if type(data.get("round")) is not int or data["round"] != 3:
        raise InventoryError(f"expected explicit round 3; got {data.get('round')!r}")
    return parse_inventory(data)


def _check_parameters(sku: str | None, category: str | None,
                      allowed_grades: Collection[str] | None,
                      min_hours_left: float | None) -> set[str] | None:
    for name, value in (("sku", sku), ("category", category)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be a nonempty string or None")
    if sku is None and category is None:
        raise ValueError("specify an exact SKU or category")
    if min_hours_left is not None and (
        isinstance(min_hours_left, bool)
        or not isinstance(min_hours_left, (int, float))
        or not math.isfinite(min_hours_left)
        or min_hours_left < 0
    ):
        raise ValueError("min_hours_left must be a finite nonnegative number")
    if allowed_grades is None:
        return None
    if isinstance(allowed_grades, str):
        raise ValueError("allowed_grades must be a collection of grade strings")
    try:
        grades = set(allowed_grades)
    except (TypeError, ValueError) as exc:
        raise ValueError("allowed_grades must be a collection of grade strings") from exc
    if not grades or any(not isinstance(g, str) or not g.strip() for g in grades):
        raise ValueError("allowed_grades must contain nonempty grade strings")
    return grades


def _matches(unit: InventoryUnit, *, sku: str | None, category: str | None,
             grades: set[str] | None, min_hours_left: float | None, now: float) -> bool:
    if sku is not None and unit.sku != sku:
        return False
    if category is not None and unit.category != category:
        return False
    if grades is not None and unit.grade not in grades:
        return False
    # A freshness condition cannot be verified for a unit without an expiry.
    if min_hours_left is not None and (unit.expires_at is None or
                                       unit.expires_at - now < min_hours_left * 3600):
        return False
    return True


def select_stock(market_dir: Path, *, sku: str | None = None, category: str | None = None,
                 allowed_grades: Collection[str] | None = None,
                 min_hours_left: float | None = None, now: float | None = None) -> StockChoice | None:
    """Choose an eligible unit; FIFO by expiry, then supply time, then unit ID.

    Returns None when no unit fits. No freshness or grade guarantee is added
    unless requested. Cost is rounded up to give an integer, cost-only floor.
    """
    grades = _check_parameters(sku, category, allowed_grades, min_hours_left)
    current = time.time() if now is None else now
    units = available_units(_round3_units(market_dir), now=current)
    eligible = [u for u in units if _matches(u, sku=sku, category=category, grades=grades,
                                              min_hours_left=min_hours_left, now=current)]
    eligible.sort(key=lambda u: (u.expires_at if u.expires_at is not None else math.inf,
                                  u.supplied_at if u.supplied_at is not None else math.inf,
                                  u.unit))
    if not eligible:
        return None
    unit = eligible[0]
    return StockChoice(unit=unit, reserve=max(1, math.ceil(unit.cost)))


def revalidate_stock(market_dir: Path, choice: StockChoice, *,
                     allowed_grades: Collection[str] | None = None,
                     min_hours_left: float | None = None, now: float | None = None) -> InventoryUnit:
    """Re-read selected unit and requirements; raises if its identity or cost changed.

    Revalidation only checks the snapshot and cannot prevent concurrent sale.
    """
    original = choice.unit
    grades = _check_parameters(original.sku, original.category, allowed_grades, min_hours_left)
    current = time.time() if now is None else now
    units = available_units(_round3_units(market_dir), now=current)
    unit = next((u for u in units if u.unit == original.unit), None)
    if (unit is None or unit.sku != original.sku or unit.cost != original.cost
            or not _matches(unit, sku=original.sku, category=original.category,
                            grades=grades, min_hours_left=min_hours_left, now=current)):
        raise InventoryError("selected unit no longer matches the current inventory snapshot")
    return unit





