"""Long-held goods leave the unit cap and clear at cost, never below it."""

from __future__ import annotations

from r3.endgame import ROUND_END
from r3.market_files import CatalogItem, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import AGED_GOODS_HOURS, DemandSignal, RestockState, plan
from r3.seller import AGED_GOODS_SECONDS, anchor_margin

NOW = ROUND_END - 4 * 86400


def bed(n: int, hours_ago: float) -> Unit:
    return Unit(
        unit=f"u-{n}",
        sku=f"old-{n}",
        category="beds",
        kind="goods",
        list_price=600_000,
        cost=450_000,
        supplied_at=NOW - hours_ago * 3600,
    )


def test_aged_goods_do_not_fill_the_unit_cap() -> None:
    catalog = {
        "hot": CatalogItem(sku="hot", category="beds", kind="goods", list_price=900_000)
    }
    policy = RestockPolicy(
        growth_enabled=False,
        depth_skus=1,
        units_per_sku=20,
        max_units=10,
        reserve_cash=1_500_000,
        stock_share=0.8,
        goods_min_list_price=200_000,
        goods_max_list_price=5_000_000,
        food_share=0,
    )
    signal = DemandSignal({"beds": 500}, {"hot": 300})
    stale = Inventory(
        round=3,
        balance=100_000_000,
        units=tuple(bed(n, AGED_GOODS_HOURS + 1) for n in range(10)),
    )
    fresh = stale.model_copy(update={"units": tuple(bed(n, 1) for n in range(10))})
    stale_lines, _ = plan(stale, catalog, (), policy, RestockState(), NOW, signal)
    fresh_lines, _ = plan(fresh, catalog, (), policy, RestockState(), NOW, signal)
    assert any(line["sku"] == "hot" for line in stale_lines)
    assert not fresh_lines


def test_aged_goods_clear_at_cost_others_keep_their_margin() -> None:
    old = bed(1, AGED_GOODS_SECONDS / 3600 + 1)
    new = bed(2, 1)
    assert anchor_margin(old, NOW, 0.05) == 0.0
    assert anchor_margin(new, NOW, 0.05) == 0.05
    food = old.model_copy(update={"kind": "food"})
    assert anchor_margin(food, NOW, 0.05) == 0.05
