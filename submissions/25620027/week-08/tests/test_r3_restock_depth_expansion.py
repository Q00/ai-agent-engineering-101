"""Owner-authorized depth breadth expands without loosening quantity or cash guards."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from r3.endgame import ROUND_END
from r3.market_files import CatalogItem, Inventory, Unit
from r3.policy import (
    Band,
    Policy,
    PolicyConflictError,
    PolicyHistory,
    RestockPolicy,
    apply_changes,
    load_history,
    load_policy,
    save_policy,
    save_tactical,
)
from r3.restock import DEPTH_SETTLES, DemandSignal, RestockState, plan

if TYPE_CHECKING:
    from pathlib import Path


def fixture() -> tuple[dict[str, CatalogItem], Inventory, DemandSignal]:
    catalog = {
        f"sku-{n:02}": CatalogItem(
            sku=f"sku-{n:02}",
            category="beds",
            kind="goods",
            list_price=1_000_000 - n * 1_000,
        )
        for n in range(25)
    }
    inventory = Inventory(
        round=3,
        balance=100_000_000,
        units=tuple(
            Unit(
                grade="A",
                unit=f"unit-{n}-{i}",
                sku=f"sku-{n:02}",
                category="beds",
                list_price=catalog[f"sku-{n:02}"].list_price,
                cost=600_000,
                supplied_at=ROUND_END - 11 * 86400,
            )
            for n in range(20)
            for i in range(2)
        ),
    )
    signal = DemandSignal({"beds": 2_500}, dict.fromkeys(catalog, 2 * DEPTH_SETTLES))
    return catalog, inventory, signal


def expanded(**changes: int) -> RestockPolicy:
    return RestockPolicy(
        growth_enabled=False,
        depth_skus=25,
        units_per_sku=20,
        max_units=changes.get("max_units", 500),
        reserve_cash=1_500_000,
        stock_share=0.8,
        goods_min_list_price=200_000,
        goods_max_list_price=5_000_000,
        food_share=0,
    )


def test_next_five_market_skus_replenish_when_first_twenty_are_stocked() -> None:
    catalog, inventory, signal = fixture()
    baseline, _ = plan(
        inventory,
        catalog,
        (),
        expanded().model_copy(update={"depth_skus": 20}),
        RestockState(),
        ROUND_END - 10 * 86400,
        signal,
    )
    lines, _ = plan(
        inventory,
        catalog,
        (),
        expanded(),
        RestockState(),
        ROUND_END - 10 * 86400,
        signal,
    )
    assert baseline == []
    assert {str(row["sku"]): row["qty"] for row in lines} == {
        f"sku-{n:02}": 2 for n in range(20, 25)
    }


def test_expanded_breadth_respects_existing_total_inventory_limit() -> None:
    catalog, inventory, signal = fixture()
    lines, _ = plan(
        inventory,
        catalog,
        (),
        expanded(max_units=44),
        RestockState(),
        ROUND_END - 10 * 86400,
        signal,
    )
    assert len(inventory.units) + sum(int(str(row["qty"])) for row in lines) == 44


def test_expanded_breadth_cannot_spend_the_retained_cash() -> None:
    catalog, inventory, signal = fixture()
    inventory = inventory.model_copy(update={"balance": 1_500_000})
    lines, _ = plan(
        inventory,
        catalog,
        (),
        expanded(),
        RestockState(),
        ROUND_END - 10 * 86400,
        signal,
    )
    assert lines == []


def test_depth_change_preserves_other_overlay_and_compare_and_swap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy.json"
    original = Policy(
        restock=RestockPolicy(growth_enabled=False, depth_skus=20, units_per_sku=20)
    )
    _ = path.write_text(PolicyHistory(current=original).model_dump_json())
    now = time.time()
    _ = save_tactical(
        path,
        {"buyer.quick_accept_ratio": 0.76},
        "independent buyer experiment",
        now=now,
        expected_version=original.version,
    )
    before = load_history(path)
    published = save_policy(
        path,
        apply_changes(before.base(), {"restock.depth_skus": 25}),
        "owner: broaden replenishment coverage",
        bands={**before.bands, "restock.depth_skus": Band(low=18, high=25)},
        expected_version=before.current.version,
    )
    after = load_history(path)
    assert published.restock.depth_skus == 25
    assert after.field_expiries == before.field_expiries
    assert after.tactical is not None
    assert before.tactical is not None
    assert after.tactical.changes == before.tactical.changes
    assert load_policy(path, now=now + 60).buyer.quick_accept_ratio == 0.76
    assert after.current.seller == before.current.seller
    assert after.current.restock.units_per_sku == 20
    assert after.current.restock.growth_enabled is False
    checkpoint = path.read_bytes()
    with pytest.raises(PolicyConflictError):
        _ = save_policy(
            path,
            before.base(),
            "stale operator",
            expected_version=before.current.version,
        )
    assert path.read_bytes() == checkpoint


def test_breadth_expansion_does_not_expand_per_sku_holding_limit() -> None:
    with pytest.raises(ValidationError):
        _ = RestockPolicy(depth_skus=25, units_per_sku=21)
