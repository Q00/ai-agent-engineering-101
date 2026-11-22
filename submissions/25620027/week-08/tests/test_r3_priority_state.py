"""Malformed priority state must never recreate a cumulative budget."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from r3.market_files import RestockWindow
from r3.priority_restock import STATE_FILE, BuyLine, PriorityState, priority_lines
from r3.restock import DemandSignal, run
from tests.test_r3_priority_restock import (
    ITEM,
    NOW,
    POLICY,
    RESULT,
    SKU,
    inventory,
    prepare,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("damage", ["missing_cost", "wrong_sum", "missing_state"])
def test_valid_json_damage_never_resets_budget(tmp_path: Path, damage: str) -> None:
    # Given: a real first emission has already reserved 2.25M.
    folder = prepare(tmp_path)
    _ = run(folder, signal=DemandSignal({}, {}), now=NOW)
    state_path = tmp_path / STATE_FILE
    state = PriorityState.model_validate_json(state_path.read_bytes()).model_dump()
    if damage == "missing_cost":
        del state["reserved_cost"]
    if damage == "wrong_sum":
        state["reserved_cost"] = 0
    if damage == "missing_state":
        state_path.unlink()
    else:
        _ = state_path.write_text(json.dumps(state))
    # When: another fresh inventory/window arrives after a process restart.
    later = inventory().model_copy(
        update={
            "restock": RestockWindow(
                opens_at=NOW + 1200,
                closes_at=NOW + 1380,
            )
        }
    )
    _ = (folder / "inventory.json").write_text(later.model_dump_json())
    os.utime(folder / "inventory.json", (NOW + 1201, NOW + 1201))
    result = RESULT.validate_json(
        run(folder, signal=DemandSignal({}, {}), now=NOW + 1201)
    )
    # Then: damage preserves the ordinary plan and adds no new commitment.
    assert result == {"buy": []}


def test_stale_inventory_disables_only_priority(tmp_path: Path) -> None:
    # Given: valid review/config but kit inventory older than 30 seconds.
    folder = prepare(tmp_path)
    os.utime(folder / "inventory.json", (NOW - 31, NOW - 31))
    # When: the extra-plan boundary sees the stale source.
    result = priority_lines(
        tmp_path,
        inventory(),
        {SKU: ITEM},
        POLICY,
        json.dumps([{"sku": "ordinary", "qty": 1}]),
        1,
        NOW,
    )
    # Then: the ordinary line is untouched and no top-up reservation appears.
    assert result.lines == (BuyLine(sku="ordinary", qty=1),)
    assert (
        PriorityState.model_validate_json(
            (tmp_path / STATE_FILE).read_bytes()
        ).reserved_cost
        == 0
    )
