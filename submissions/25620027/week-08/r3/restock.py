"""Restock hook: breadth first, then replenish what sells, never below the cash line.

The kit runs this every 60 seconds and buys at most ten lines. Visibility in Round 3
is stock: a buyer's shop list shows only shops holding an acceptable SKU, and the
house buyers pick randomly among them. So the first hours spread one or two units
over many categories chosen for low risk (food with a 30-day shelf life, mid-priced
A-grade goods), then the plan follows the public ledger's settled categories and our
own sales. Costs are drawn by the market, so every line is checked against a
worst-case cost and a cash reserve before it is printed.
"""

from __future__ import annotations

import contextlib
import json
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import Field, ValidationError

from market_a2a.models import FrozenModel
from r3.endgame import buy_share
from r3.market_files import (
    CatalogItem,
    DemandRow,
    Inventory,
    RestockWindow,
    read_catalog,
    read_demand,
    read_inventory,
)
from r3.policy import RestockPolicy, load_policy, policy_path

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

STATE_FILE: Final = "r3-restock-state.json"
MAX_LINES: Final = 10
# The market draws a unit's cost at 55-75% of list for grade A; cash checks use the
# top of that range. The policy table cannot lower it.
WORST_CASE_COST_SHARE: Final = 0.75
# The kit retries a refused line three times, sleeping up to 30 s each, so a plan
# printed further than this before the window opens spends its lines on 429s.
WINDOW_LEAD_SECONDS: Final = 30.0
# Owner rule (11/4): no food with under a week of shelf life, whatever the
# strategy sets; 24 h food aged into clearance and sold below cost.
OWNER_MIN_FOOD_SHELF_HOURS: Final = 168
# Owner rule (11/4): high-value goods first. A goods category's score is scaled
# by the median list price of its fitting SKUs over this unit (never below 1):
# a unit at ~0.78 of list on ~0.60 cost earns ~18% of list, so one 420k mattress
# earns what about 28 cheap food sales do.
VALUE_UNIT: Final = 100_000


class RestockState(FrozenModel):
    """What this hook remembers between runs (its own file, not the kit's)."""

    first_run_at: float = 0.0
    units_seen: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    last_sold_at: dict[str, float] = Field(default_factory=dict)
    planned_cost: int = 0


@dataclass(frozen=True, slots=True)
class DemandSignal:
    """Settled-deal counts from the public ledger, when the collector has them.

    ``hot_categories`` and ``cold_categories`` come from the scout's picture: the
    model read forum requests and settlement counts and named categories from the
    market's own demand list (anything else was dropped before it got here).
    """

    by_category: Mapping[str, int]
    by_sku: Mapping[str, int]
    hot_categories: frozenset[str] = frozenset()
    cold_categories: frozenset[str] = frozenset()


def load_state(path: Path) -> RestockState:
    """Read the hook's own state; corrupt or missing means a fresh start."""
    try:
        return RestockState.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return RestockState()


def save_state(path: Path, state: RestockState) -> None:
    """Write the state atomically."""
    tmp = path.with_suffix(".tmp")
    _ = tmp.write_text(state.model_dump_json(), encoding="utf-8")
    _ = tmp.replace(path)


def sku_fits(item: CatalogItem, policy: RestockPolicy) -> bool:
    """Risk filter: long-life food in a mid price band, mid-priced goods."""
    if item.kind == "food":
        return (
            (item.shelf_hours or 0)
            >= max(policy.min_food_shelf_hours, OWNER_MIN_FOOD_SHELF_HOURS)
            and policy.food_min_list_price
            <= item.list_price
            <= policy.food_max_list_price
        )
    return policy.goods_min_list_price <= item.list_price <= policy.goods_max_list_price


def stock_budget(balance: int, stock_cost: int, policy: RestockPolicy) -> int:
    """Return the most stock (at cost) to hold: a share of equity, never a fixed cap.

    Equity is cash plus stock at cost, so the budget grows as the shop earns and
    shrinks after losses; ``seed_budget`` is the floor while the shop is small.
    The cash reserve still applies separately.
    """
    return max(policy.seed_budget, round(policy.stock_share * (balance + stock_cost)))


def category_scores(
    demand: tuple[DemandRow, ...],
    catalog: dict[str, CatalogItem],
    signal: DemandSignal | None,
    policy: RestockPolicy,
) -> list[tuple[float, DemandRow]]:
    """Rank categories: settled deals, scaled for goods by typical list price."""
    fitting: dict[str, list[int]] = {}
    for item in catalog.values():
        if sku_fits(item, policy):
            fitting.setdefault(item.category, []).append(item.list_price)
    scored: list[tuple[float, DemandRow]] = []
    for row in demand:
        prices = sorted(fitting.get(row.category, ()))
        candidates = len(prices)
        if candidates == 0:
            continue
        base = 1.0 if row.kind == "goods" else 1.5
        if signal is not None:
            base += 2.0 * signal.by_category.get(row.category, 0)
            if row.category in signal.hot_categories:
                base += 1.5
            if row.category in signal.cold_categories:
                base *= 0.5
        if row.kind == "goods":
            base *= max(1.0, prices[candidates // 2] / VALUE_UNIT)
        scored.append((base * min(1.0, candidates / 5.0), row))
    scored.sort(key=lambda pair: -pair[0])
    return scored


def pick_sku(  # noqa: PLR0913 - explicit inputs
    category: str,
    catalog: dict[str, CatalogItem],
    signal: DemandSignal | None,
    policy: RestockPolicy,
    *,
    rng: random.Random,
    already: set[str],
) -> CatalogItem | None:
    """Choose a SKU: the settled one with the most takings, else a random fit."""
    fitting = [
        item
        for item in catalog.values()
        if item.category == category
        and sku_fits(item, policy)
        and item.sku not in already
    ]
    if not fitting:
        return None
    if signal is not None:
        settled = [i for i in fitting if signal.by_sku.get(i.sku, 0) > 0]
        if settled:
            # expected takings, not settle count: a dear SKU that sells beats a
            # cheap one that sells a little more often
            return max(
                settled,
                key=lambda i: (
                    signal.by_sku.get(i.sku, 0) * i.list_price,
                    i.list_price,
                ),
            )
    return rng.choice(fitting)


def plan(  # noqa: PLR0913, PLR0912, PLR0915, PLR0917, C901 - one explicit pass
    inventory: Inventory,
    catalog: dict[str, CatalogItem],
    demand: tuple[DemandRow, ...],
    policy: RestockPolicy,
    state: RestockState,
    now: float,
    signal: DemandSignal | None = None,
) -> tuple[list[dict[str, object]], RestockState]:
    """Return the kit's ``buy`` lines and the updated hook state."""
    if inventory.round < 3:  # noqa: PLR2004 - the market's economy round
        return [], state
    first_run = state.first_run_at or now
    seen_now: dict[str, list[str]] = {}
    for unit in inventory.units:
        seen_now.setdefault(unit.sku, []).append(unit.unit)
    sold_at = dict(state.last_sold_at)
    for sku, previous in state.units_seen.items():
        gone = set(previous) - set(seen_now.get(sku, ()))
        if gone:
            sold_at[sku] = now
    stock_cost = sum(u.cost for u in inventory.units)
    cash = inventory.balance - policy.reserve_cash
    budget = round(stock_budget(inventory.balance, stock_cost, policy) * buy_share(now))
    lines: list[dict[str, object]] = []
    spent = 0
    food_lines = 0
    food_cap = round(MAX_LINES * policy.food_share)
    stocked = {u.sku for u in inventory.units}
    stocked_categories: dict[str, int] = {}
    for unit in inventory.units:
        stocked_categories[unit.category] = stocked_categories.get(unit.category, 0) + 1

    def affordable(item: CatalogItem, qty: int) -> int:
        """Return the quantity the reserve and stock caps allow (0 when none)."""
        nonlocal spent
        unit_cost = int(item.list_price * WORST_CASE_COST_SHARE)
        if unit_cost <= 0:
            return 0
        room = min(cash - spent, budget - stock_cost - spent)
        allowed = min(qty, room // unit_cost, policy.max_units - units_total)
        if allowed <= 0:
            return 0
        spent += unit_cost * allowed
        return allowed

    units_total = len(inventory.units)
    # 1. Replenish what just sold, while demand for it is fresh.
    for sku, stamp in sorted(sold_at.items(), key=lambda kv: -kv[1]):
        if len(lines) >= MAX_LINES or units_total >= policy.max_units:
            break
        if now - stamp > policy.stale_hours * 3600 or sku in stocked:
            continue
        item = catalog.get(sku)
        if item is None or not sku_fits(item, policy):
            continue
        if item.kind == "food" and food_lines >= food_cap:
            continue
        qty = affordable(item, policy.replenish_qty)
        if qty:
            lines.append({"sku": sku, "qty": qty})
            units_total += qty
            if item.kind == "food":
                food_lines += 1
    # 2. Breadth: new categories, long-life food and mid-priced goods first.
    rng = random.Random(int(now // 3600))  # noqa: S311 - variety, not security
    chosen_skus = set(stocked) | {str(line["sku"]) for line in lines}
    for _, row in category_scores(demand, catalog, signal, policy):
        if len(lines) >= min(MAX_LINES, policy.categories_per_run):
            break
        if units_total >= policy.max_units:
            break
        if stocked_categories.get(row.category, 0) >= policy.units_per_sku * 2:
            continue
        if row.kind == "food" and food_lines >= food_cap:
            continue
        item = pick_sku(
            row.category, catalog, signal, policy, rng=rng, already=chosen_skus
        )
        if item is None:
            continue
        qty = affordable(item, policy.units_per_sku)
        if not qty:
            continue
        lines.append({"sku": item.sku, "qty": qty})
        chosen_skus.add(item.sku)
        stocked_categories[row.category] = stocked_categories.get(row.category, 0) + 1
        units_total += qty
        if item.kind == "food":
            food_lines += 1
    updated = state.model_copy(
        update={
            "first_run_at": first_run,
            "units_seen": {sku: tuple(units) for sku, units in seen_now.items()},
            "last_sold_at": sold_at,
            "planned_cost": spent,
        }
    )
    return lines[:MAX_LINES], updated


def supply_closed(window: RestockWindow | None, now: float) -> bool:
    """Return True while the supply window stays shut past the kit's retry wait.

    Half the lines printed on 11/4 (237 of 521) hit a closed window and were lost;
    the hook now prints nothing until the window is about to open.
    """
    if window is None or window.open is not False:
        return False
    return window.opens_at - now > WINDOW_LEAD_SECONDS


def run(
    folder: Path,
    *,
    policy_file: Path | None = None,
    signal: DemandSignal | None = None,
    now: float | None = None,
) -> str:
    """Read the kit's files, plan, persist state and return the JSON line to print."""
    stamp = now if now is not None else time.time()
    policy = load_policy(
        policy_file if policy_file is not None else policy_path(folder)
    )
    inventory = read_inventory(folder)
    if supply_closed(inventory.restock, stamp):
        return json.dumps({"buy": []})
    state_path = folder.resolve().parent / STATE_FILE
    state = load_state(state_path)
    lines, updated = plan(
        inventory,
        read_catalog(folder),
        read_demand(folder),
        policy.restock,
        state,
        stamp,
        signal,
    )
    with contextlib.suppress(OSError):
        save_state(state_path, updated)
    return json.dumps({"buy": lines}, ensure_ascii=False)
