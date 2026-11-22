"""Owner-approved, cumulative-budget top-ups; ordinary restocking stays intact."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from market_a2a.models import FrozenModel
from r3.endgame import buy_share
from r3.policy_storage import atomic_snapshot, locked

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from r3.market_files import CatalogItem, Inventory
    from r3.policy import RestockPolicy

PROGRAM_FILE: Final = "r3-priority-restock.json"
STATE_FILE: Final = "r3-priority-restock-state.json"
DISABLED_FILE: Final = "r3-priority-restock.disabled"
DELEGATION_END: Final = 1794841200.0
MAX_EVIDENCE_AGE: Final = 5400
MAX_INVENTORY_AGE: Final = 30
MAX_BUDGET: Final = 100_000_000
MAX_LINES: Final = 10
MAX_ADDED_PER_SKU: Final = 3
WORST_COST: Final = 0.75
EXPECTED_A_COST: Final = 0.65
MIN_LIST: Final = 500_000


class PrioritySku(FrozenModel):
    """A reviewed total position and conservative historical recovery ceiling."""

    sku: str = Field(min_length=1, strict=True)
    target_units: int = Field(ge=1, le=20, strict=True)
    max_expected_unit_cost: int = Field(gt=0, strict=True)


class PriorityProgram(FrozenModel):
    """Versioned owner authorization, never written by the strategy model."""

    version: int = Field(ge=1, strict=True)
    program_id: str = Field(min_length=1, strict=True)
    approved_budget: int = Field(ge=0, le=MAX_BUDGET, strict=True)
    evidence_at: float = Field(ge=0, allow_inf_nan=False)
    valid_until: float = Field(gt=0, le=DELEGATION_END, allow_inf_nan=False)
    refund_reserve: int = Field(default=0, ge=0, strict=True)
    skus: tuple[PrioritySku, ...] = Field(min_length=1, max_length=5)


class BuyLine(FrozenModel):
    """Only the market's existing SKU/quantity fields cross the hook boundary."""

    sku: str
    qty: int = Field(gt=0, le=20, strict=True)


class PriorityReservation(FrozenModel):
    """Worst-case incremental commitment, not an actual supply receipt or profit."""

    at: float = Field(ge=0, allow_inf_nan=False, strict=True)
    window_open: float = Field(gt=0, allow_inf_nan=False, strict=True)
    added: tuple[BuyLine, ...]
    worst_cost: int = Field(ge=0, strict=True)
    displaced: tuple[BuyLine, ...] = ()


class PriorityState(FrozenModel):
    """Survives restarts; failed/corrupt state never resets the spending allowance."""

    program_id: str = Field(min_length=1, strict=True)
    version: int = Field(ge=0, strict=True)
    reserved_cost: int = Field(ge=0, strict=True)
    reservations: tuple[PriorityReservation, ...]

    @model_validator(mode="after")
    def coherent_budget(self) -> Self:
        """Missing or inconsistent commitment history cannot recreate cash room."""
        if (
            self.version != len(self.reservations)
            or self.reserved_cost != sum(row.worst_cost for row in self.reservations)
            or len({row.window_open for row in self.reservations})
            != len(self.reservations)
        ):
            message = "Priority reservations do not match cumulative commitment"
            raise ValueError(message)
        return self


class PriorityConflictError(RuntimeError):
    """Another owner update won the compare-and-swap."""


@locked
def save_program(
    path: Path, program: PriorityProgram, *, expected_version: int
) -> None:
    """Publish only on the exact owner-reviewed version; preserve program identity."""
    previous = None
    if path.exists():
        previous = PriorityProgram.model_validate_json(path.read_bytes())
    actual_version = previous.version if previous is not None else 0
    if actual_version != expected_version or program.version != actual_version + 1:
        raise PriorityConflictError
    if previous is not None and previous.program_id != program.program_id:
        raise PriorityConflictError
    if len({row.sku for row in program.skus}) != len(program.skus):
        raise PriorityConflictError
    state_path = path.with_name(STATE_FILE)
    if state_path.exists():
        state = PriorityState.model_validate_json(state_path.read_bytes())
        if state.program_id != program.program_id:
            raise PriorityConflictError
    elif previous is not None:
        raise PriorityConflictError
    else:
        state = PriorityState(
            program_id=program.program_id, version=0, reserved_cost=0, reservations=()
        )
        atomic_snapshot(state_path, state.model_dump_json())
    atomic_snapshot(path, program.model_dump_json())


@dataclass(frozen=True, slots=True)
class PriorityResult:
    """A complete replacement plan and its conservative cash commitment."""

    lines: tuple[BuyLine, ...]
    extra_cost: int = 0


@locked
def apply_program(  # noqa: PLR0911, PLR0913, PLR0917, C901 - atomic shared-budget pass
    path: Path,
    inventory: Inventory,
    catalog: Mapping[str, CatalogItem],
    policy: RestockPolicy,
    baseline: Sequence[BuyLine],
    baseline_cost: int,
    now: float,
) -> PriorityResult:
    """Reserve once per real window; failed receipts never recreate budget."""
    unchanged = PriorityResult(tuple(baseline))
    if path.with_name(DISABLED_FILE).exists() or not path.exists():
        return unchanged
    window = inventory.restock
    if (
        window is None
        or not 0 < window.opens_at <= now < window.closes_at
        or inventory.round != 3  # noqa: PLR2004 - explicit market round
        or buy_share(now) <= 0
        or not math.isfinite(now)
    ):
        return unchanged
    program = PriorityProgram.model_validate_json(path.read_bytes())
    if (
        not 0 <= now - program.evidence_at <= MAX_EVIDENCE_AGE
        or now >= program.valid_until
        or len({row.sku for row in program.skus}) != len(program.skus)
    ):
        return unchanged
    state_path = path.with_name(STATE_FILE)
    state = PriorityState.model_validate_json(state_path.read_bytes())
    if state.program_id != program.program_id or any(
        row.window_open == window.opens_at for row in state.reservations
    ):
        return unchanged
    allowance = program.approved_budget - state.reserved_cost
    held: dict[str, int] = {}
    for unit in inventory.units:
        held[unit.sku] = held.get(unit.sku, 0) + 1
    planned = {row.sku: row.qty for row in baseline}
    if len(planned) != len(baseline):
        return unchanged
    surge = {row.sku: row.factor for row in inventory.surge}
    stock_cost = sum(unit.cost for unit in inventory.units)
    reserve = max(policy.reserve_cash, program.refund_reserve)
    stock_limit = max(
        policy.seed_budget, round(policy.stock_share * (inventory.balance + stock_cost))
    )
    room = min(
        inventory.balance - reserve - baseline_cost,
        round(stock_limit * buy_share(now)) - stock_cost - baseline_cost,
        allowance,
    )
    unit_room = policy.max_units - len(inventory.units) - sum(planned.values())
    added: list[BuyLine] = []
    spent = 0
    for target in program.skus:
        item = catalog.get(target.sku)
        if (
            item is None
            or item.kind != "goods"
            or not max(MIN_LIST, policy.goods_min_list_price)
            <= item.list_price
            <= policy.goods_max_list_price
            or target.sku in policy.probe_skus
        ):
            continue
        factor = surge.get(target.sku, 1.0)
        worst = math.ceil(item.list_price * WORST_COST * factor)
        expected = math.ceil(item.list_price * EXPECTED_A_COST * factor)
        if expected >= target.max_expected_unit_cost:
            continue
        qty = min(
            MAX_ADDED_PER_SKU,
            target.target_units - held.get(target.sku, 0) - planned.get(target.sku, 0),
            min(20, policy.units_per_sku)
            - held.get(target.sku, 0)
            - planned.get(target.sku, 0),
            (room - spent) // worst,
            unit_room,
        )
        if qty <= 0:
            continue
        added.append(BuyLine(sku=target.sku, qty=qty))
        spent += worst * qty
        unit_room -= qty
    if not added:
        return unchanged
    increments = {row.sku: row.qty for row in added}
    # Keep the existing cheap trial's reserved line, then owner-priority stock.
    protected = tuple(row for row in baseline if row.sku in policy.probe_skus)
    priority = tuple(
        BuyLine(sku=row.sku, qty=planned.get(row.sku, 0) + row.qty) for row in added
    )
    other = tuple(
        row for row in baseline if row.sku not in increments and row not in protected
    )
    result = (*protected, *priority, *other)[:MAX_LINES]
    if any(row not in result for row in priority):
        return unchanged
    displaced = tuple(row for row in baseline if all(x.sku != row.sku for x in result))
    reservation = PriorityReservation(
        at=now,
        window_open=window.opens_at,
        added=tuple(added),
        worst_cost=spent,
        displaced=displaced,
    )
    updated = state.model_copy(
        update={
            "version": state.version + 1,
            "reserved_cost": state.reserved_cost + spent,
            "reservations": (*state.reservations, reservation),
        }
    )
    atomic_snapshot(state_path, updated.model_dump_json())
    return PriorityResult(tuple(result), spent)


def priority_lines(  # noqa: PLR0913, PLR0917 - independent hook inputs
    root: Path,
    inventory: Inventory,
    catalog: Mapping[str, CatalogItem],
    policy: RestockPolicy,
    baseline_json: str,
    baseline_cost: int,
    now: float,
) -> PriorityResult:
    """Bad owner evidence/state disables only extra purchases, preserving baseline."""
    baseline = TypeAdapter(tuple[BuyLine, ...]).validate_json(baseline_json)
    try:
        program_path = root / PROGRAM_FILE
        if not program_path.exists() or root.joinpath(DISABLED_FILE).exists():
            return PriorityResult(baseline)
        inventory_at = (root / ".market/inventory.json").stat().st_mtime
        if not 0 <= now - inventory_at <= MAX_INVENTORY_AGE:
            return PriorityResult(baseline)
        return apply_program(
            program_path,
            inventory,
            catalog,
            policy,
            baseline,
            baseline_cost,
            now,
        )
    except (OSError, ValidationError):
        return PriorityResult(baseline)
