"""Rebuy follows our own 12 h sales: slow SKUs stay thin, fast ones go deep."""

from __future__ import annotations

from typing import Final

from r3.endgame import ROUND_END
from r3.market_files import CatalogItem, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import NEW_SKU_STOP_UNITS, DemandSignal, RestockState, plan

NOW: Final = ROUND_END - 10 * 86400
SOFA: Final = CatalogItem(
    sku="sofa", category="sofas", kind="goods", list_price=2_976_600
)


def rebuy(sold_12h: int | None) -> int:
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        replenish_qty=10,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        goods_min_list_price=0,
        goods_max_list_price=5_000_000,
        food_share=0,
    )
    state = RestockState(
        last_sold_at={"sofa": NOW - 600}, sold_log={"sofa": [NOW - 600]}
    )
    # A better earner keeps the sofa out of the top-net boost (economic_target).
    economics = (
        None
        if sold_12h is None
        else {"sofa": (300_000.0, sold_12h)}
        | {f"other{i}": (900_000.0, 10) for i in range(4)}
    )
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=()),
        {"sofa": SOFA},
        (),
        policy,
        state,
        NOW,
        DemandSignal({}, {}),
        economics,
    )
    return sum(int(str(row["qty"])) for row in lines if row["sku"] == "sofa")


def test_slow_seller_is_rebought_to_its_own_sales_not_replenish_qty() -> None:
    assert rebuy(1) == 2
    assert rebuy(3) == 3


def test_fast_seller_goes_past_replenish_qty_up_to_the_sku_cap() -> None:
    assert rebuy(16) == 16
    assert rebuy(40) == 20


def test_without_economics_replenish_qty_still_applies() -> None:
    assert rebuy(None) == 10


def depth_buy(sold_12h: int) -> int:
    """Return the buy of a market best seller (depth line) we sell ``sold_12h`` of."""
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        replenish_qty=10,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        goods_min_list_price=0,
        goods_max_list_price=5_000_000,
        food_share=0,
    )
    economics = {"sofa": (300_000.0, sold_12h)} | {
        f"other{i}": (900_000.0, 10) for i in range(4)
    }
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=()),
        {"sofa": SOFA},
        (),
        policy,
        RestockState(),
        NOW,
        DemandSignal({}, {"sofa": 30 * 15}),
        economics,
    )
    return sum(int(str(row["qty"])) for row in lines if row["sku"] == "sofa")


def test_depth_line_is_capped_by_our_own_sales() -> None:
    # The market sells 450 a day (depth 15); we sold 4 in 12 h -> hold at most 8.
    # 11/15 22:0x: 4 or fewer sold in 12 h holds its 12 h sales (~2 x 6 h), was 8.
    assert depth_buy(4) == 4
    assert depth_buy(0) == 3
    assert depth_buy(10) == 15


def test_food_is_rebought_to_its_12h_sales_within_the_sku_cap() -> None:
    # 11/15 14:0x: FOOD_SALES_SHARE 0.5 -> 1.0; 30 sold in 12 h, capped at 20.
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        replenish_qty=10,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        food_share=1.0,
    )
    water = CatalogItem(
        sku="water", category="water", kind="food", list_price=5_400, shelf_hours=720
    )
    state = RestockState(
        last_sold_at={"water": NOW - 600}, sold_log={"water": [NOW - 600]}
    )
    economics = {"water": (300.0, 30)} | {f"o{i}": (9_000.0, 10) for i in range(4)}
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=()),
        {"water": water},
        (),
        policy,
        state,
        NOW,
        DemandSignal({}, {}),
        economics,
    )
    assert sum(int(str(r["qty"])) for r in lines if r["sku"] == "water") == 20


def c_pile_buy(sold_12h: int, held_c: int) -> int:
    """Return the depth buy of a goods SKU that holds ``held_c`` grade C units."""
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        replenish_qty=10,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        goods_min_list_price=0,
        goods_max_list_price=5_000_000,
        food_share=0,
    )
    units = tuple(
        Unit(
            unit=f"c{i}",
            sku="sofa",
            category="sofas",
            kind="goods",
            list_price=SOFA.list_price,
            cost=900_000,
            grade="C",
            supplied_at=NOW - 3600,
        )
        for i in range(held_c)
    )
    economics = {"sofa": (300_000.0, sold_12h)} | {
        f"other{i}": (900_000.0, 10) for i in range(4)
    }
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=units),
        {"sofa": SOFA},
        (),
        policy,
        RestockState(),
        NOW,
        DemandSignal({}, {"sofa": 30 * 15}),
        economics,
    )
    return sum(int(str(row["qty"])) for row in lines if row["sku"] == "sofa")


def test_piled_c_takes_room_from_slow_goods_only() -> None:
    assert c_pile_buy(4, 0) == 4  # slow goods cap (11/15 22:0x), was 8
    assert c_pile_buy(4, 6) == 1  # 6 C take 3 of the 4
    assert c_pile_buy(12, 6) == 15  # a fast seller keeps its full room


def named_kimchi_buy(now: float, sold_12h: int = 0) -> int:
    """Return the buy of q00's kimchi G5f5f42e0 at ``now``."""
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        replenish_qty=10,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        food_share=1.0,
        min_food_shelf_hours=168,
    )
    kimchi = CatalogItem(
        sku="G5f5f42e0",
        category="김치",
        kind="food",
        list_price=47_900,
        shelf_hours=168,
    )
    economics = {"G5f5f42e0": (3_000.0, sold_12h)} | {
        f"o{i}": (9_000.0, 10) for i in range(4)
    }
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=()),
        {"G5f5f42e0": kimchi},
        (),
        policy,
        RestockState(),
        now,
        DemandSignal({}, {}),
        economics,
    )
    return sum(int(str(r["qty"])) for r in lines if r["sku"] == "G5f5f42e0")


def test_q00_kimchi_is_trialled_then_follows_sales_and_stops_near_the_end() -> None:
    assert named_kimchi_buy(NOW) == 2
    assert named_kimchi_buy(NOW, sold_12h=12) == 12
    assert named_kimchi_buy(ROUND_END - 40 * 3600) == 0


def test_no_new_sku_is_started_above_the_unit_stop() -> None:
    filler = tuple(
        Unit(
            unit=f"f{i}",
            sku="filler",
            category="x",
            kind="goods",
            list_price=300_000,
            cost=100,
            grade="A",
            supplied_at=NOW - 3600,
        )
        for i in range(NEW_SKU_STOP_UNITS)
    )
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=filler),
        {"sofa": SOFA},
        (),
        RestockPolicy(
            growth_enabled=False,
            units_per_sku=20,
            max_units=5000,
            depth_skus=1,
            seed_budget=0,
            reserve_cash=0,
            stock_share=0.8,
            goods_min_list_price=0,
            goods_max_list_price=5_000_000,
            food_share=0,
        ),
        RestockState(),
        NOW,
        DemandSignal({}, {}),
        {"sofa": (300_000.0, 4)} | {f"o{i}": (900_000.0, 10) for i in range(4)},
        rater_skus=("sofa",),
    )
    assert not any(row["sku"] == "sofa" for row in lines)


def test_fast_goods_hold_about_four_hours_of_sales() -> None:
    from r3.restock import fast_goods_cap  # noqa: PLC0415 - one helper

    # 11/15 17:0x: 40+ settled in 12 h -> a third of it, at most 40; others keep 20.
    assert fast_goods_cap(39, 20) == 20
    assert fast_goods_cap(60, 20) == 20
    assert fast_goods_cap(90, 20) == 30
    assert fast_goods_cap(200, 20) == 40


def test_slow_or_losing_goods_hold_about_two_six_hour_sales() -> None:
    from r3.restock import fast_goods_cap  # noqa: PLC0415 - one helper

    # 11/15 22:0x: losing money per unit or 4 or fewer sold in 12 h.
    assert fast_goods_cap(4, 20, 1000.0) == 4
    assert fast_goods_cap(0, 20, 1000.0) == 3
    assert fast_goods_cap(50, 20, -500.0) == 20  # never above the base cap
    assert fast_goods_cap(10, 20, -1.0) == 10
    assert fast_goods_cap(90, 20, 1000.0) == 30  # fast sellers unchanged


def test_replenish_gives_scarce_food_lines_to_the_top_seller_first() -> None:
    # 11/16: with one food line, the SKU that sold most in 12 h gets it, even if
    # the slower one sold more recently.
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        replenish_qty=10,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        food_share=0.1,
    )
    fast = CatalogItem(
        sku="fast", category="kimchi", kind="food", list_price=9_000, shelf_hours=720
    )
    slow = CatalogItem(
        sku="slow", category="kimchi", kind="food", list_price=9_000, shelf_hours=720
    )
    state = RestockState(
        last_sold_at={"fast": NOW - 900, "slow": NOW - 60},
        sold_log={"fast": [NOW - 900], "slow": [NOW - 60]},
    )
    economics = {"fast": (500.0, 150), "slow": (500.0, 5)} | {
        f"o{i}": (9_000.0, 10) for i in range(4)
    }
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=()),
        {"fast": fast, "slow": slow},
        (),
        policy,
        state,
        NOW,
        DemandSignal({}, {}),
        economics,
    )
    assert [line["sku"] for line in lines] == ["fast"]
