"""A finite owner allocation changes real hook output without duplicating cash."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from r3.endgame import ROUND_END
from r3.market_files import CatalogItem, Inventory, RestockWindow, SurgePrice, Unit
from r3.policy import Policy, PolicyHistory, RestockPolicy
from r3.priority_restock import (
    DISABLED_FILE,
    PROGRAM_FILE,
    STATE_FILE,
    BuyLine,
    PriorityConflictError,
    PriorityProgram,
    PrioritySku,
    PriorityState,
    apply_program,
    save_program,
)
from r3.restock import DemandSignal, run

if TYPE_CHECKING:
    from pathlib import Path

NOW = ROUND_END - 9 * 86400
SKU = "priority"
ITEM = CatalogItem(sku=SKU, name="verified seller", kind="goods", list_price=1_000_000)
POLICY = RestockPolicy(
    growth_enabled=False,
    goods_min_list_price=0,
    goods_max_list_price=5_000_000,
    stock_share=0.8,
    max_units=500,
    units_per_sku=20,
)
RESULT = TypeAdapter(dict[str, list[BuyLine]])


def program() -> PriorityProgram:
    return PriorityProgram(
        version=1,
        program_id="owner-program",
        approved_budget=30_000_000,
        evidence_at=NOW,
        valid_until=NOW + 3600,
        skus=(PrioritySku(sku=SKU, target_units=6, max_expected_unit_cost=680_000),),
    )


def inventory(held: int = 0) -> Inventory:
    return Inventory(
        round=3,
        balance=100_000_000,
        restock=RestockWindow(open=True, opens_at=NOW - 5, closes_at=NOW + 175),
        units=tuple(Unit(unit=f"u-{n}", sku=SKU, cost=600_000) for n in range(held)),
    )


def authorize(root: Path, settings: PriorityProgram | None = None) -> Path:
    path = root / PROGRAM_FILE
    save_program(path, settings or program(), expected_version=0)
    return path


def test_total_position_includes_baseline_plan(tmp_path: Path) -> None:
    # Given: three held plus two already planned against a six-unit target.
    path = authorize(tmp_path)
    # When: owner priority tops up the real combined position.
    result = apply_program(
        path,
        inventory(3),
        {SKU: ITEM},
        POLICY,
        (BuyLine(sku=SKU, qty=2),),
        1_500_000,
        NOW,
    )
    # Then: only one more unit is reserved, and one merged market line is emitted.
    assert result.lines == (BuyLine(sku=SKU, qty=3),)
    assert result.extra_cost == 750_000
    assert (
        PriorityState.model_validate_json(
            (tmp_path / STATE_FILE).read_bytes()
        ).reserved_cost
        == 750_000
    )


def test_same_window_and_restart_do_not_repeat_increment(tmp_path: Path) -> None:
    # Given: a completed priority emission persisted before the hook returned.
    path = authorize(tmp_path)
    first = apply_program(path, inventory(), {SKU: ITEM}, POLICY, (), 0, NOW)
    assert first.lines == (BuyLine(sku=SKU, qty=3),)
    # When: another process sees the same still-stale inventory in that window.
    second = apply_program(path, inventory(), {SKU: ITEM}, POLICY, (), 0, NOW + 60)
    # Then: its unchanged baseline has no second incremental commitment.
    assert second.lines == ()
    assert (
        PriorityState.model_validate_json(
            (tmp_path / STATE_FILE).read_bytes()
        ).reserved_cost
        == 2_250_000
    )


def test_budget_is_cumulative_across_windows(tmp_path: Path) -> None:
    # Given: budget reserves three units, even if no actual receipt arrives.
    path = authorize(
        tmp_path, program().model_copy(update={"approved_budget": 3_000_000})
    )
    _ = apply_program(path, inventory(), {SKU: ITEM}, POLICY, (), 0, NOW)
    later = inventory().model_copy(
        update={
            "restock": RestockWindow(
                open=True, opens_at=NOW + 1200, closes_at=NOW + 1380
            )
        }
    )
    # When: the next real supply window plans another top-up.
    result = apply_program(path, later, {SKU: ITEM}, POLICY, (), 0, NOW + 1201)
    # Then: only the remaining one-unit worst-case budget is available.
    assert result.lines == (BuyLine(sku=SKU, qty=1),)
    assert (
        PriorityState.model_validate_json(
            (tmp_path / STATE_FILE).read_bytes()
        ).reserved_cost
        == 3_000_000
    )


@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "expired",
        "future",
        "surge",
        "probe",
        "endgame",
        "no_window",
        "too_early",
        "totalcap",
        "cash",
        "stockshare",
    ],
)
def test_guards_preserve_baseline(  # noqa: C901 - independent guard matrix
    tmp_path: Path, change: str
) -> None:
    settings = program()
    inv = inventory()
    policy = POLICY
    now = NOW
    if change == "stale":
        settings = settings.model_copy(update={"evidence_at": NOW - 5401})
    if change == "expired":
        settings = settings.model_copy(update={"valid_until": NOW})
    if change == "future":
        settings = settings.model_copy(update={"evidence_at": NOW + 1})
    if change == "surge":
        inv = inv.model_copy(update={"surge": (SurgePrice(sku=SKU, factor=2),)})
    if change == "probe":
        policy = policy.model_copy(update={"probe_skus": (SKU,)})
    if change == "endgame":
        now = ROUND_END - 3600
    if change == "no_window":
        inv = inv.model_copy(update={"restock": None})
    if change == "too_early":
        inv = inv.model_copy(
            update={"restock": RestockWindow(opens_at=NOW + 20, closes_at=NOW + 200)}
        )
    if change == "totalcap":
        policy = policy.model_copy(update={"max_units": 1})
    if change == "cash":
        inv = inv.model_copy(update={"balance": 1_500_000})
    if change == "stockshare":
        policy = policy.model_copy(update={"stock_share": 0, "seed_budget": 0})
    path = authorize(tmp_path, settings)
    baseline = (BuyLine(sku="ordinary", qty=1),)
    result = apply_program(path, inv, {SKU: ITEM}, policy, baseline, 750_000, now)
    assert result.lines == baseline
    state = PriorityState.model_validate_json((tmp_path / STATE_FILE).read_bytes())
    assert state.reserved_cost == 0
    assert state.reservations == ()


def test_disabled_marker_preserves_other_plan(tmp_path: Path) -> None:
    path = authorize(tmp_path)
    (tmp_path / DISABLED_FILE).touch()
    result = apply_program(
        path,
        inventory(),
        {SKU: ITEM},
        POLICY,
        (BuyLine(sku="ordinary", qty=1),),
        1,
        NOW,
    )
    assert result.lines == (BuyLine(sku="ordinary", qty=1),)


def test_stale_cas_and_program_reset_are_rejected(tmp_path: Path) -> None:
    path = authorize(tmp_path)
    next_version = program().model_copy(
        update={"version": 2, "approved_budget": 50_000_000}
    )
    with pytest.raises(PriorityConflictError):
        save_program(path, next_version, expected_version=0)
    save_program(path, next_version, expected_version=1)
    with pytest.raises(PriorityConflictError):
        save_program(
            path,
            next_version.model_copy(update={"version": 3, "program_id": "reset"}),
            expected_version=2,
        )


def test_corrupt_reservation_disables_only_topups_in_actual_hook(
    tmp_path: Path,
) -> None:
    folder = prepare(tmp_path)
    _ = (tmp_path / STATE_FILE).write_text("corrupt")
    result = RESULT.validate_json(run(folder, signal=DemandSignal({}, {}), now=NOW))
    assert result == {"buy": []}
    assert (tmp_path / STATE_FILE).read_text() == "corrupt"


def prepare(root: Path) -> Path:
    folder = root / ".market"
    folder.mkdir()
    _ = (folder / "catalog.json").write_text(json.dumps({SKU: ITEM.model_dump()}))
    _ = (folder / "inventory.json").write_text(inventory().model_dump_json())
    os.utime(folder / "inventory.json", (NOW, NOW))
    _ = (folder / "demand.json").write_text("[]")
    _ = (root / "r3-policy.json").write_text(
        PolicyHistory(current=Policy(restock=POLICY)).model_dump_json()
    )
    _ = authorize(root)
    return folder


def test_real_hook_emits_only_authorized_three_units(tmp_path: Path) -> None:
    # Given: no ordinary market-depth targets, and a reviewed owner allocation.
    folder = prepare(tmp_path)
    # When: the production restock entrypoint runs in its real files.
    result = RESULT.validate_json(run(folder, signal=DemandSignal({}, {}), now=NOW))
    # Then: it emits the existing market wire format and persists its commitment.
    assert result == {"buy": [BuyLine(sku=SKU, qty=3)]}
    assert (
        PriorityState.model_validate_json(
            (tmp_path / STATE_FILE).read_bytes()
        ).reserved_cost
        == 2_250_000
    )


def test_program_update_does_not_reset_reserved_budget(tmp_path: Path) -> None:
    path = authorize(tmp_path)
    _ = apply_program(path, inventory(), {SKU: ITEM}, POLICY, (), 0, NOW)
    save_program(
        path,
        program().model_copy(update={"version": 2, "approved_budget": 50_000_000}),
        expected_version=1,
    )
    assert (
        PriorityState.model_validate_json(
            (tmp_path / STATE_FILE).read_bytes()
        ).reserved_cost
        == 2_250_000
    )


def test_priority_displaces_last_line_and_keeps_named_probe(tmp_path: Path) -> None:
    path = authorize(tmp_path)
    policy = POLICY.model_copy(update={"probe_skus": ("cheap",)})
    baseline = (
        BuyLine(sku="cheap", qty=1),
        *(BuyLine(sku=f"core-{i}", qty=1) for i in range(9)),
    )
    result = apply_program(path, inventory(), {SKU: ITEM}, policy, baseline, 1, NOW)
    assert len(result.lines) == 10
    assert result.lines[0].sku == "cheap"
    assert result.lines[1].sku == SKU
    state = PriorityState.model_validate_json((tmp_path / STATE_FILE).read_bytes())
    assert state.reservations[0].displaced == (BuyLine(sku="core-8", qty=1),)
