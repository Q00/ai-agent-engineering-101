"""Pure seller pricing: stock facts plus the policy table give every number.

No model, no network. The seller server calls these functions on every turn, so a
turn costs microseconds and the price can be explained from the unit's cost, grade,
age and the policy version that was active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from r3.market_files import Unit
    from r3.policy import SellerPolicy


@dataclass(frozen=True, slots=True)
class Quote:
    """The opening ask, the lowest price we accept, and the facts behind them."""

    ask: int
    accept_at: int
    floor: int
    grade_now: str
    clearance: bool
    stale: bool


def grade_now(unit: Unit, now: float, shelf_hours: float | None) -> str:
    """Grade the market would certify now (food decays; goods keep their grade)."""
    return unit.current_food_grade(now, shelf_hours)


def claimed_grade(
    unit: Unit, now: float, shelf_hours: float | None, policy: SellerPolicy
) -> str:
    """Claim the certified grade only while it holds through checkout and settlement.

    The market judges a claim against the grade at checkout or at settlement. A
    lower claim is not a safe claim: it is wrong at both moments unless the grade
    actually drops. So the claim is the current grade, or nothing when the grade
    would change within the policy's horizon.
    """
    grade = grade_now(unit, now, shelf_hours)
    if unit.shelf_left_ratio(now, shelf_hours) is None:
        return grade
    later = grade_now(unit, now + policy.claim_horizon_seconds, shelf_hours)
    return grade if later == grade else ""


def quote(
    unit: Unit, now: float, shelf_hours: float | None, policy: SellerPolicy
) -> Quote:
    """Price one unit from its cost, grade, remaining life and age."""
    grade = grade_now(unit, now, shelf_hours)
    cost = max(1, unit.cost)
    list_price = max(cost, unit.list_price)
    ratio = unit.shelf_left_ratio(now, shelf_hours)
    clearance = ratio is not None and ratio < policy.clearance_shelf_ratio
    age_hours = max(0.0, (now - unit.supplied_at) / 3600.0) if unit.supplied_at else 0.0
    stale = unit.kind == "goods" and age_hours >= policy.stale_goods_hours
    markup = policy.markup(grade) - (policy.stale_markup_cut if stale else 0.0)
    if clearance:
        floor = max(1, round(cost * policy.clearance_floor_of_cost))
        accept_at = floor
        ask = max(floor, round(list_price * max(0.25, markup) * 0.8))
    else:
        floor = max(1, round(cost * (1.0 + policy.min_margin)))
        accept_at = max(floor, round(cost * (1.0 + policy.accept_margin)))
        ask = max(accept_at, round(list_price * max(0.25, markup)))
    ask = min(ask, list_price) if list_price >= accept_at else accept_at
    return Quote(ask, accept_at, floor, grade, clearance, stale)


def counter(ask: int, accept_at: int, policy: SellerPolicy) -> int:
    """Move the ask toward the acceptance line by the policy's concession share."""
    if ask <= accept_at:
        return accept_at
    step = max(1, round((ask - accept_at) * policy.concession))
    return max(accept_at, ask - step)


def decide(
    offer: int | None, ask: int, accept_at: int, rounds: int, policy: SellerPolicy
) -> tuple[str, int]:
    """Return ``("accept", price)``, ``("counter", new_ask)`` or ``("reject", 0)``."""
    if offer is not None and offer >= accept_at:
        return "accept", offer
    if rounds >= policy.max_rounds:
        return "reject", 0
    return "counter", counter(ask, accept_at, policy)
