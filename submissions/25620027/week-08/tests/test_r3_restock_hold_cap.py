"""Inventory plus every buy path stays inside the twenty-unit SKU limit."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from r3.endgame import ROUND_END
from r3.growth import GrowthEvidence, SkuGrowth
from r3.market_files import CatalogItem, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import DEPTH_SETTLES, DemandSignal, RestockState, plan

if TYPE_CHECKING:
    from collections.abc import Mapping

NOW: Final = ROUND_END - 10 * 86400
PRODUCT: Final = CatalogItem(
    sku="winner", category="beds", kind="goods", list_price=1_611_000
)


def inventory(held: int, balance: int = 300_000_000) -> Inventory:
    return Inventory(
        round=3,
        balance=balance,
        units=tuple(
            Unit(
                grade="A",
                unit=f"u-{n}",
                sku=PRODUCT.sku,
                category=PRODUCT.category,
                list_price=PRODUCT.list_price,
                cost=800_000,
                supplied_at=NOW - 3600,
            )
            for n in range(held)
        ),
    )


def policy(**changes: bool | int) -> RestockPolicy:
    return RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        goods_min_list_price=0,
        goods_max_list_price=5_000_000,
        food_share=0,
    ).model_copy(update=changes)


def quantity(lines: list[dict[str, object]]) -> int:
    return sum(int(str(row["qty"])) for row in lines if row["sku"] == PRODUCT.sku)


def stock_plan(
    held: int,
    *,
    own_rate: int = 0,
    economics: Mapping[str, tuple[float, int]] | None = None,
    restock_policy: RestockPolicy | None = None,
) -> tuple[list[dict[str, object]], RestockState]:
    state = RestockState(
        last_sold_at={PRODUCT.sku: NOW - 60} if own_rate else {},
        sold_log={PRODUCT.sku: [NOW - 60] * own_rate} if own_rate else {},
    )
    return plan(
        inventory(held),
        {PRODUCT.sku: PRODUCT},
        (),
        restock_policy or policy(),
        state,
        NOW,
        DemandSignal({}, {PRODUCT.sku: 500 if own_rate else 19 * DEPTH_SETTLES}),
        economics,
    )


@pytest.mark.parametrize(
    # 11/15 17:0x: 240 settled in 12 h makes it a fast goods SKU whose A/B cap is
    # a third of that, at most 40 (r3.restock.fast_goods_cap); boost target is 29.
    ("held", "expected"),
    # One line is at most 20 units (r3.restock.MAX_LINE_QTY).
    [(0, 20), (10, 19), (19, 10), (20, 9), (22, 7)],
)
def test_economic_boost_cannot_buy_past_combined_holdings(
    held: int, expected: int
) -> None:
    # Market target19 becomes29 for a fast winner: held10 must buy10, not19.
    lines, _ = stock_plan(held, economics={PRODUCT.sku: (500_000, 240)})
    assert quantity(lines) == expected
    assert held + quantity(lines) <= max(held, 40)


def test_depth_and_own_replenishment_share_the_remaining_sku_capacity() -> None:
    lines, _ = stock_plan(10, own_rate=30, economics={PRODUCT.sku: (500_000, 240)})
    assert len(lines) == 1
    assert quantity(lines) == 20  # cap 40 (11/15 17:0x), one line at most 20


def test_under_cap_demand_is_still_replenished_without_economic_boost() -> None:
    lines, _ = stock_plan(10)
    assert quantity(lines) == 9


def test_total_inventory_limit_can_be_tighter_than_the_sku_limit() -> None:
    lines, _ = stock_plan(
        18,
        economics={PRODUCT.sku: (500_000, 240)},
        restock_policy=policy(max_units=19),
    )
    assert quantity(lines) == 1


def test_cash_reserve_still_blocks_an_unaffordable_probe_or_depth_buy() -> None:
    lines, updated = plan(
        inventory(0, balance=1_500_000),
        {PRODUCT.sku: PRODUCT},
        (),
        policy(reserve_cash=1_500_000),
        RestockState(),
        NOW,
        DemandSignal({}, {PRODUCT.sku: 950}),
        {PRODUCT.sku: (500_000, 240)},
    )
    assert lines == []
    assert updated.planned_cost == 0


def test_growth_mode_retains_its_valid_last_two_units_of_coverage() -> None:
    lines, _ = plan(
        inventory(18),
        {PRODUCT.sku: PRODUCT},
        (),
        policy(growth_enabled=True),
        RestockState(),
        NOW,
        DemandSignal({}, {PRODUCT.sku: 1000}),
        growth=GrowthEvidence(
            {
                PRODUCT.sku: SkuGrowth(
                    sales_1h=30,
                    in_stock_hours=1,
                    mature_sales=20,
                    mature_net=4_000_000,
                    mature_cost=16_000_000,
                    median_hold_hours=1,
                )
            }
        ),
    )
    assert quantity(lines) == 2
