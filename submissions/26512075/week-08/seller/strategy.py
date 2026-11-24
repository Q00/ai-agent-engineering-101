"""Configurable B-first policy. B++ is a markup target, never a grade claim."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time


@dataclass(frozen=True)
class Policy:
    target_markup: float = 0.15
    minimum_markup: float = 0.05
    min_food_hours: float = 1.0
    clearance_food_hours: float = 3.0
    aged_a_hours: float = 24.0
    clearance_cost_fraction: float = 0.9  # Legacy config accepted; no longer permits below-cost sales.
    allowed_grades: tuple = ("A", "B", "C")
    offered_warranty_hours: float = 0.0
    turnover_markup: float | None = None

    def __post_init__(self):
        if self.turnover_markup is not None and (type(self.turnover_markup) not in (int, float)
                or not math.isfinite(self.turnover_markup) or self.turnover_markup <= 0):
            raise ValueError("turnover_markup must be positive")
        for key in ("target_markup", "minimum_markup", "min_food_hours",
                    "clearance_food_hours", "aged_a_hours", "clearance_cost_fraction",
                    "offered_warranty_hours"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {key}")
        if self.target_markup < self.minimum_markup or self.clearance_cost_fraction > 1:
            raise ValueError("invalid margin settings")
        if not self.allowed_grades or any(g not in ("A", "B", "C") for g in self.allowed_grades):
            raise ValueError("allowed_grades must contain A/B/C")
        if self.offered_warranty_hours != 0:
            raise ValueError("positive warranties disabled until full refund reservations are integrated")

    @classmethod
    def load(cls):
        path = os.getenv("SELLER_POLICY")
        return cls(**json.loads(Path(path).read_text())) if path else cls()


def hours_left(unit, now):
    return (unit.expires_at - now) / 3600 if unit.expires_at is not None else None


def pricing(unit, policy, now):
    held = max(0, now - unit.supplied_at) / 3600 if unit.supplied_at is not None else 0
    remaining = hours_left(unit, now)
    clearance = unit.grade in ("B", "C") or held >= policy.aged_a_hours
    storage = unit.cost * .01 * held / 24 if unit.kind == "goods" else 0
    # Grade-specific reserves exclude storage and do not fall with stock age.
    floor = unit.cost * (200 if unit.grade == "A" else 106) / 100
    near_clearance = unit.grade == "A" and held >= 18
    target = floor if clearance or near_clearance else unit.cost * (1 + policy.target_markup) + storage
    if policy.turnover_markup is not None:
        target = max(floor, unit.cost + 1, math.ceil(unit.cost * (1 + policy.turnover_markup)))
    return {"reserve": max(1, math.ceil(floor)), "target_price": max(1, math.ceil(max(floor, target))),
            "pricing_mode": "turnover" if policy.turnover_markup is not None else "clearance" if clearance else "aged_floor" if near_clearance else "normal", "storage_cost": storage,
            "grade": unit.grade, "remaining_shelf_life_hours": remaining,
            "offered_warranty_hours": 0}


def eligible(unit, policy, now, min_hours=None, grades=None):
    required = max(policy.min_food_hours, min_hours or 0)
    return (unit.grade in policy.allowed_grades and (grades is None or unit.grade in grades)
            and (unit.kind != "food" or (hours_left(unit, now) is not None and hours_left(unit, now) >= required)))


def stock_order(unit):
    return ({"B": 0, "A": 1, "C": 2}.get(unit.grade, 3),
            unit.expires_at if unit.expires_at is not None else math.inf,
            unit.supplied_at if unit.supplied_at is not None else math.inf, unit.unit)


def comps(state, unit, now=None):
    now = time.time() if now is None else now
    try:
        report = json.loads((Path(state) / "forum-candidates.json").read_text())
        if report.get("market") != os.getenv("MARKET_URL", "https://market.ourolabs.ai").rstrip("/"):
            return None
        if not -5 <= now - report["generated_at"] <= 300:
            return None
        for row in report.get("condition_candidates", []):
            if (row.get("sku") == unit.sku and row.get("grade") == unit.grade
                    and row.get("warranty_hours") == 0 and row.get("warranty_status") == "known"
                    and row.get("sample_settlements", 0) >= 3):
                value = row["sample_median_settled_price"]
                if type(value) in (int, float) and math.isfinite(value) and value > 0:
                    return {"median": value, "sample_count": row["sample_settlements"],
                            "sku": unit.sku, "grade": unit.grade, "warranty_hours": 0,
                            "source": "official_ledger", "generated_at": report["generated_at"]}
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None
