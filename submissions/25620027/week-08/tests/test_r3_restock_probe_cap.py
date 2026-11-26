"""Owner-named trial positions remain bounded through every restock path."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from pydantic import JsonValue, TypeAdapter

from r3.endgame import ROUND_END
from r3.growth import GrowthEvidence, SkuGrowth
from r3.market_files import CatalogItem, DemandRow, Inventory, Unit
from r3.policy import (
    Policy,
    PolicyHistory,
    RestockPolicy,
    SellerPolicy,
    apply_changes,
    load_history,
    load_policy,
    save_policy,
    save_tactical,
)
from r3.restock import DemandSignal, RestockState, plan

if TYPE_CHECKING:
    from pathlib import Path

NOW: Final = ROUND_END - 10 * 86400
SKU: Final = "trial"
PRODUCT: Final = CatalogItem(
    sku=SKU, category="beds", kind="goods", list_price=1_000_000
)


def inventory(held: int, *, balance: int = 300_000_000) -> Inventory:
    return Inventory(
        round=3,
        balance=balance,
        units=tuple(
            Unit(
                grade="A",
                unit=f"u-{n}",
                sku=SKU,
                category=PRODUCT.category,
                list_price=PRODUCT.list_price,
                cost=600_000,
                supplied_at=NOW - 3600,
            )
            for n in range(held)
        ),
    )


def policy(
    *,
    named: tuple[str, ...] = (SKU,),
    growth: bool = False,
    max_units: int = 500,
    reserve_cash: int = 0,
) -> RestockPolicy:
    base = RestockPolicy(
        growth_enabled=growth,
        units_per_sku=20,
        depth_skus=25,
        seed_budget=0,
        reserve_cash=reserve_cash,
        stock_share=0.8,
        goods_min_list_price=0,
        goods_max_list_price=5_000_000,
        food_share=0,
        max_units=max_units,
    )
    # Old-code replay deliberately bypasses the not-yet-added policy field so
    # the red regressions exercise the unbounded planner, not schema rejection.
    return base.model_copy(update={"probe_skus": named})


def quantity(lines: list[dict[str, object]]) -> int:
    return sum(int(str(row["qty"])) for row in lines if row["sku"] == SKU)


@pytest.mark.parametrize(("held", "expected"), [(0, 3), (1, 2), (2, 1), (3, 0), (5, 0)])
def test_stockout_target_cannot_expand_named_trial_position(
    held: int, expected: int
) -> None:
    lines, updated = plan(
        inventory(held),
        {SKU: PRODUCT},
        (),
        policy(),
        RestockState(),
        NOW,
        DemandSignal({}, {SKU: 100}),
        stockouts={SKU: (10, 0.5)},
    )
    assert quantity(lines) == expected
    assert held + quantity(lines) <= max(held, 3)
    assert updated.planned_cost == expected * 750_000
    assert len(updated.units_seen.get(SKU, ())) == held


@pytest.mark.parametrize("boost", [False, True])
def test_recent_sale_replenishment_and_economic_boost_share_probe_limit(
    *, boost: bool
) -> None:
    lines, _ = plan(
        inventory(1),
        {SKU: PRODUCT},
        (),
        policy(),
        RestockState(last_sold_at={SKU: NOW - 60}, sold_log={SKU: [NOW - 60] * 30}),
        NOW,
        economics={SKU: (500_000, 240)} if boost else None,
    )
    assert quantity(lines) == 2


def test_depth_line_and_following_own_replenishment_share_one_probe_position() -> None:
    lines, _ = plan(
        inventory(1),
        {SKU: PRODUCT},
        (),
        policy(),
        RestockState(last_sold_at={SKU: NOW - 60}, sold_log={SKU: [NOW - 60] * 20}),
        NOW,
        DemandSignal({}, {SKU: 100}),
    )
    assert len(lines) == 1
    assert quantity(lines) == 2


@pytest.mark.parametrize("named", [(), ("other-trial",)])
def test_empty_or_unrelated_probe_list_preserves_existing_replenishment(
    named: tuple[str, ...],
) -> None:
    lines, _ = plan(
        inventory(0),
        {SKU: PRODUCT},
        (),
        policy(named=named),
        RestockState(),
        NOW,
        stockouts={SKU: (10, 0.5)},
    )
    assert quantity(lines) == 6


def test_owner_probe_cap_also_applies_to_growth_mode() -> None:
    lines, _ = plan(
        inventory(1),
        {SKU: PRODUCT},
        (),
        policy(growth=True),
        RestockState(),
        NOW,
        DemandSignal({}, {SKU: 1000}),
        growth=GrowthEvidence(
            {
                SKU: SkuGrowth(
                    sales_1h=30,
                    in_stock_hours=1,
                    mature_sales=20,
                    mature_net=4_000_000,
                    mature_cost=12_000_000,
                    median_hold_hours=1,
                )
            }
        ),
    )
    assert quantity(lines) == 2


def test_breadth_trial_keeps_its_existing_two_unit_start() -> None:
    lines, _ = plan(
        inventory(0),
        {SKU: PRODUCT},
        (DemandRow(category=PRODUCT.category, kind="goods"),),
        policy(),
        RestockState(),
        NOW,
    )
    assert quantity(lines) == 2


def test_total_and_cash_constraints_can_be_tighter_than_probe_cap() -> None:
    lines, _ = plan(
        inventory(1),
        {SKU: PRODUCT},
        (),
        policy(max_units=2),
        RestockState(),
        NOW,
        stockouts={SKU: (10, 0.5)},
    )
    assert quantity(lines) == 1
    lines, updated = plan(
        inventory(0, balance=1_500_000),
        {SKU: PRODUCT},
        (),
        policy(reserve_cash=1_500_000),
        RestockState(),
        NOW,
        stockouts={SKU: (10, 0.5)},
    )
    assert lines == []
    assert updated.planned_cost == 0


def test_probe_list_cas_roundtrip_preserves_72h_depth25_and_overlay_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("r3.policy.time.time", lambda: 150.0)
    path = tmp_path / "policy.json"
    baseline = Policy(
        seller=SellerPolicy(warranty_a=72, warranty_b=72, warranty_c=72),
        restock=policy(named=()),
    )
    _ = path.write_text(PolicyHistory(current=baseline).model_dump_json())
    _ = save_tactical(
        path,
        {"buyer.late_accept_share": 0.49},
        "existing buyer overlay",
        now=100,
        ttl=100,
        expected_version=baseline.version,
    )
    before = load_history(path)
    candidate = apply_changes(before.base(), {"restock.probe_skus": [SKU]})
    saved = save_policy(
        path, candidate, "bound trial only", expected_version=before.current.version
    )
    current = load_history(path)
    assert saved.restock.probe_skus == (SKU,)
    assert current.field_expiries == before.field_expiries
    assert current.field_expiries["buyer.late_accept_share"] == 200
    assert current.current.restock.depth_skus == 25
    assert current.current.seller == baseline.seller
    assert load_policy(path, now=201).restock.probe_skus == (SKU,)
    assert load_policy(path, now=201).buyer.late_accept_share == 0.45
    cleared = save_policy(
        path,
        apply_changes(current.base(), {"restock.probe_skus": []}),
        "clear this trial field only",
        expected_version=current.current.version,
    )
    assert cleared.restock.probe_skus == ()
    assert cleared.restock.depth_skus == 25
    assert cleared.seller == baseline.seller
    assert load_history(path).field_expiries == before.field_expiries


def test_legacy_serialized_policy_without_probe_field_keeps_empty_default() -> None:
    raw = TypeAdapter(dict[str, JsonValue]).validate_json(
        PolicyHistory(
            current=Policy(
                seller=SellerPolicy(warranty_a=72, warranty_b=72, warranty_c=72),
                restock=policy(named=()),
            )
        ).model_dump_json()
    )
    current = raw["current"]
    assert isinstance(current, dict)
    restock = current["restock"]
    assert isinstance(restock, dict)
    _ = restock.pop("probe_skus", None)
    parsed = PolicyHistory.model_validate(raw)
    assert parsed.current.restock.probe_skus == ()
    assert parsed.current.restock.depth_skus == 25
    assert parsed.current.seller.warranty_c == 72
