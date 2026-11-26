"""Pure seller pricing: stock facts plus the policy table give every number.

No model, no network. The seller server calls these functions on every turn, so a
turn costs microseconds and the price can be explained from the unit's cost, grade,
age and the policy version that was active.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from r3.endgame import (
    LIQUIDATION_ASK_CUT,
    LIQUIDATION_FLOOR_OF_COST,
    ROUND_END,
    sell_pressure,
)

if TYPE_CHECKING:
    from r3.market_files import Unit
    from r3.policy import SellerPolicy

# Initial rating pilot for cheap A goods; the configured minimum margin still wins.
CHEAP_A_ACCEPT_MARGIN: Final = 0.08


@dataclass(frozen=True, slots=True)
class Quote:
    """The opening ask, the lowest price we accept, and the facts behind them."""

    ask: int
    accept_at: int
    floor: int
    grade_now: str
    clearance: bool
    stale: bool
    endgame: float = 0.0


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
        if grade == "C" and policy.accept_margin_c is not None:
            margin = policy.accept_margin_c
        elif (
            policy.accept_margin_high is not None
            and list_price >= policy.high_list_price
        ):
            margin = policy.accept_margin_high
        elif (
            unit.kind == "goods"
            and grade == "A"
            and list_price < policy.cheap_list_price
        ):
            margin = min(policy.accept_margin, CHEAP_A_ACCEPT_MARGIN)
        else:
            margin = policy.accept_margin
        accept_at = max(floor, round(cost * (1.0 + margin)))
        ask = max(accept_at, round(list_price * max(0.25, markup)))
    cap = (
        policy.cheap_max_ask_ratio
        if list_price < policy.cheap_list_price
        else policy.max_ask_ratio
    )
    ask = max(accept_at, min(ask, round(list_price * cap)))
    ask = min(ask, list_price) if list_price >= accept_at else accept_at
    pressure = sell_pressure(now)
    if pressure > 0:
        # Book retains unsold cost; only food lost by closing may liquidate.
        expires_by_close = (
            unit.kind == "food"
            and unit.expires_at is not None
            and unit.expires_at <= ROUND_END
        )
        liquidation = (
            max(1, round(cost * LIQUIDATION_FLOOR_OF_COST))
            if expires_by_close
            else floor
        )
        floor = min(floor, round(floor + (liquidation - floor) * pressure))
        accept_at = (
            max(floor, round(accept_at + (floor - accept_at) * pressure))
            if expires_by_close
            else accept_at
        )
        # 11/17 07:3x (owner): only food lost by closing has its ask cut. Goods C sold
        # 13.3 % lower (cost x1.523 -> 1.374) under the endgame ask cut while book is
        # profit + stock cost, so units that outlive the close keep their asks.
        if expires_by_close:
            cut = round(list_price * LIQUIDATION_ASK_CUT * pressure)
            ask = max(accept_at, ask - cut)
    return Quote(ask, accept_at, floor, grade, clearance, stale, pressure)


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


# 11/11 21:50: over 3 h our shop sold 136 units for 42.6M (same hours on 11/10:
# 221 for 107.3M) while category demand held. Buyers still came (239 sofa asks)
# but met a first ask near 0.76 of list and an accept line of cost x 1.18: beds
# cost us 0.650 of list and the market paid 0.680, bedding 0.628 vs 0.684, so
# we sold only above the market. With five or more settles of the SKU in six
# hours, the accept line comes down to just under that median and the first ask
# to a little over it, never below cost plus the policy's minimum margin.
# Step 2 (11/11 22:50, owner "판매 공격적으로 해"): after step 1 (ask x1.05,
# floor cost x1.05) conversion rose 54 -> 59 % at +34k a sale, but fewer buyers
# came (84 -> 64 an hour; buyers rank shops by what they paid before). The ask
# goes to x1.02 and the floor to cost x1.02: above cost, so Book never falls.
# Step 3 (11/13 14:0x, owner "더 잘팔거같으면 깎아줘도돼, 판매량과 수익 트레이드오프
# 상시계산"): over 11/13 10:48-14:31 KST, goods first asks at 0.64-0.66 of
# list sold 27-29 %, 0.68 sold 17 %, 0.70-0.72 sold 9-12 % and 0.74 sold 3 %;
# 575 of 864 unsold goods opens walked at the first ask. About four points of
# list lower roughly triples a sale's odds while the median margin of a sale is
# 0.097 of list, so both revenue and profit rise. Ask x0.97, accept x0.94 of the
# median; the floor stays cost x1.02, so Book never falls.
# Step 4 (11/13 17:0x, owner "더 공격적으로 해야할거같으면 지금 반영"): 2.4 h after
# step 3, goods sold 32.0 an hour for 17.24M and 1.35M profit (the same length
# before: 19.3, 9.79M, 0.84M), so goods go one step further. Food stays at the
# market (owner: "식품가격은 시장가에 항상 맞춰"), not under it.
# Step 5 (11/14 02:1x, owner "더 공격적으로 해도된다 제한 더 풀어줄게 매출 1위 가보자"):
# step 4 over 8.74 h sold goods at 62.3 an hour for 40.94M and 2.57M profit (the same
# length before step 3: 21.2, 11.63M, 1.18M). One step further; floor unchanged.
MARKET_ACCEPT_SHARE: Final = 0.88  # 0.99 to step 3, 0.94 to step 4, 0.91 to step 5
MARKET_ASK_SHARE: Final = 0.91  # 1.02 to step 3, 0.97 to step 4, 0.94 to step 5
FOOD_ACCEPT_SHARE: Final = 0.94
FOOD_ASK_SHARE: Final = 0.97
MARKET_MIN_MARGIN: Final = 0.02


def market_anchored(  # noqa: PLR0913 - shares default to the goods step
    quoted: Quote,
    cost: int,
    median: float | None,
    min_margin: float,
    *,
    accept_share: float = MARKET_ACCEPT_SHARE,
    ask_share: float = MARKET_ASK_SHARE,
) -> Quote:
    """Pull a goods quote down to the SKU's recent market median, above cost."""
    if median is None or median <= 0:
        return quoted
    floor = max(1, round(cost * (1.0 + min(min_margin, MARKET_MIN_MARGIN))))
    accept_at = quoted.accept_at
    if accept_at > median * accept_share:
        accept_at = max(floor, round(median * accept_share))
    ask = quoted.ask
    if ask > median * ask_share:
        ask = max(accept_at, round(median * ask_share))
    if (accept_at, ask) == (quoted.accept_at, quoted.ask):
        return quoted
    return replace(
        quoted, accept_at=accept_at, ask=ask, floor=min(quoted.floor, accept_at)
    )


# Patch 3.6 (11/12 18:19) scores a buyer by (list - paid) / (list - unit cost), and
# from 19:00 buyers bought our food only when the accept price left them about
# 0.78 of our margin: 19-22 h, sold quotes left 0.78-0.81, unsold ones 0.56-0.73
# (food sales 15-19 an hour before, 5-14 after). Our food accept line is cost x
# 1.18, so a unit whose cost came out high (0.6-0.7 of list) left the buyer too
# little. The accept line now leaves at least this share, never under the floor;
# cheap units keep their higher line.
FOOD_BUYER_SHARE: Final = 0.78


def buyer_share_capped(
    quoted: Quote, cost: int, list_price: int, share: float = FOOD_BUYER_SHARE
) -> Quote:
    """Lower the accept line so the buyer keeps ``share`` of our margin."""
    if list_price <= cost or quoted.clearance:
        return quoted
    target = round(cost + (1.0 - share) * (list_price - cost))
    accept_at = max(quoted.floor, min(quoted.accept_at, target))
    if accept_at == quoted.accept_at:
        return quoted
    return replace(quoted, accept_at=accept_at, ask=max(accept_at, quoted.ask))
