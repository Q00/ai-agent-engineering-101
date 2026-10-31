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
from r3.market_files import (
    CatalogItem,
    DemandRow,
    Inventory,
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


class RestockState(FrozenModel):
    """What this hook remembers between runs (its own file, not the kit's)."""

    first_run_at: float = 0.0
    units_seen: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    last_sold_at: dict[str, float] = Field(default_factory=dict)
    planned_cost: int = 0


@dataclass(frozen=True, slots=True)
class DemandSignal:
    """Settled-deal counts from the public ledger, when the collector has them."""

    by_category: Mapping[str, int]
    by_sku: Mapping[str, int]


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
            (item.shelf_hours or 0) >= policy.min_food_shelf_hours
            and policy.food_min_list_price
            <= item.list_price
            <= policy.food_max_list_price
        )
    return policy.goods_min_list_price <= item.list_price <= policy.goods_max_list_price


def category_scores(
    demand: tuple[DemandRow, ...],
    catalog: dict[str, CatalogItem],
    signal: DemandSignal | None,
    policy: RestockPolicy,
) -> list[tuple[float, DemandRow]]:
    """Rank categories: settled deals first, then the risk heuristic."""
    fitting: dict[str, int] = {}
    for item in catalog.values():
        if sku_fits(item, policy):
            fitting[item.category] = fitting.get(item.category, 0) + 1
    scored: list[tuple[float, DemandRow]] = []
    for row in demand:
        candidates = fitting.get(row.category, 0)
        if candidates == 0:
            continue
        base = 1.0 if row.kind == "goods" else 1.5
        if signal is not None:
            base += 2.0 * signal.by_category.get(row.category, 0)
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
    """Choose a SKU in the category: settled ones first, else a random fitting one."""
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
            return max(
                settled, key=lambda i: (signal.by_sku.get(i.sku, 0), i.list_price)
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
    lines: list[dict[str, object]] = []
    spent = 0
    stocked = {u.sku for u in inventory.units}
    stocked_categories: dict[str, int] = {}
    for unit in inventory.units:
        stocked_categories[unit.category] = stocked_categories.get(unit.category, 0) + 1

    def affordable(item: CatalogItem, qty: int) -> bool:
        nonlocal spent
        estimate = int(item.list_price * policy.cost_estimate_ratio) * qty
        if estimate <= 0 or spent + estimate > cash:
            return False
        # seed_budget caps the capital tied up in stock (at worst-case cost).
        if stock_cost + spent + estimate > policy.seed_budget:
            return False
        spent += estimate
        return True

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
        if affordable(item, policy.replenish_qty):
            lines.append({"sku": sku, "qty": policy.replenish_qty})
            units_total += policy.replenish_qty
    # 2. Breadth: new categories, long-life food and mid-priced goods first.
    rng = random.Random(int(now // 3600))  # noqa: S311 - variety, not security
    chosen_skus = set(stocked) | {str(line["sku"]) for line in lines}
    food_lines = sum(
        1
        for line in lines
        if catalog.get(str(line["sku"]), CatalogItem()).kind == "food"
    )
    for _, row in category_scores(demand, catalog, signal, policy):
        if len(lines) >= min(MAX_LINES, policy.categories_per_run):
            break
        if units_total >= policy.max_units:
            break
        if stocked_categories.get(row.category, 0) >= policy.units_per_sku * 2:
            continue
        food_cap = max(1, round(MAX_LINES * policy.food_share))
        if row.kind == "food" and food_lines >= food_cap and signal is None:
            continue
        item = pick_sku(
            row.category, catalog, signal, policy, rng=rng, already=chosen_skus
        )
        if item is None or not affordable(item, policy.units_per_sku):
            continue
        lines.append({"sku": item.sku, "qty": policy.units_per_sku})
        chosen_skus.add(item.sku)
        stocked_categories[row.category] = stocked_categories.get(row.category, 0) + 1
        units_total += policy.units_per_sku
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
    state_path = folder.resolve().parent / STATE_FILE
    state = load_state(state_path)
    lines, updated = plan(
        read_inventory(folder),
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
