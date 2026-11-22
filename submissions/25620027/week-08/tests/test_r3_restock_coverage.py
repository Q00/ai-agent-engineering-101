"""Busy categories we hold nothing in get a trial line before replenishment."""

from __future__ import annotations

from r3.endgame import ROUND_END
from r3.market_files import CatalogItem, DemandRow, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import COVERAGE_LINES, TRIAL_QTY, DemandSignal, RestockState, plan

NOW = ROUND_END - 4 * 86400


def fixture(
    grain_settles: int = 500,
) -> tuple[dict[str, CatalogItem], Inventory, DemandSignal, RestockState]:
    catalog = {
        f"bed-{n:02}": CatalogItem(
            sku=f"bed-{n:02}", category="beds", kind="goods", list_price=600_000
        )
        for n in range(12)
    }
    for cat in ("grain", "oil", "sauce"):
        catalog[f"{cat}-1"] = CatalogItem(
            sku=f"{cat}-1",
            category=cat,
            kind="food",
            list_price=20_000,
            shelf_hours=720,
        )
    inventory = Inventory(
        round=3,
        balance=100_000_000,
        units=tuple(
            Unit(
                grade="A",
                unit=f"u-{n}",
                sku=f"bed-{n:02}",
                category="beds",
                list_price=600_000,
                cost=400_000,
                supplied_at=NOW - 3600,
            )
            for n in range(12)
        ),
    )
    by_sku = dict.fromkeys(catalog, 50)
    signal = DemandSignal(
        {"beds": 600, "grain": grain_settles, "oil": 400, "sauce": 300}, by_sku
    )
    # Twelve bed SKUs sold within the hour: replenishment alone would fill every line.
    state = RestockState(
        last_sold_at={f"bed-{n:02}": NOW - 60 for n in range(12)},
        sold_log={f"bed-{n:02}": [NOW - 60, NOW - 120] for n in range(12)},
    )
    return catalog, inventory, signal, state


DEMAND = (
    DemandRow(category="beds", kind="goods"),
    DemandRow(category="grain", kind="food", shelf_hours=720),
    DemandRow(category="oil", kind="food", shelf_hours=720),
    DemandRow(category="sauce", kind="food", shelf_hours=720),
)


def policy() -> RestockPolicy:
    return RestockPolicy(
        growth_enabled=False,
        depth_skus=1,
        units_per_sku=20,
        replenish_qty=3,
        max_units=1500,
        reserve_cash=1_500_000,
        stock_share=0.8,
        goods_min_list_price=200_000,
        goods_max_list_price=5_000_000,
        min_food_shelf_hours=168,
        food_share=0.6,
    )


def test_empty_busy_categories_are_seeded_before_replenishment() -> None:
    catalog, inventory, signal, state = fixture()
    lines, _ = plan(inventory, catalog, DEMAND, policy(), state, NOW, signal)
    food = [line for line in lines if not str(line["sku"]).startswith("bed-")]
    assert len(food) == min(COVERAGE_LINES, 3)
    assert all(line["qty"] == TRIAL_QTY for line in food)


def test_quiet_or_held_categories_are_not_seeded() -> None:
    catalog, inventory, signal, state = fixture(grain_settles=50)
    held = Unit(
        grade="A",
        unit="u-oil",
        sku="oil-1",
        category="oil",
        list_price=20_000,
        cost=12_000,
        supplied_at=NOW - 3600,
    )
    inventory = inventory.model_copy(update={"units": (*inventory.units, held)})
    lines, _ = plan(inventory, catalog, DEMAND, policy(), state, NOW, signal)
    skus = {str(line["sku"]) for line in lines}
    assert "grain-1" not in skus
    assert "sauce-1" in skus


def test_thin_busy_category_gets_another_sku_until_its_target() -> None:
    catalog, inventory, signal, state = fixture()
    for n in range(2, 5):
        catalog[f"grain-{n}"] = CatalogItem(
            sku=f"grain-{n}",
            category="grain",
            kind="food",
            list_price=20_000 + n * 5_000,
            shelf_hours=720,
        )
    signal = DemandSignal(
        {"beds": 600, "grain": 900, "oil": 400, "sauce": 300},
        dict.fromkeys(catalog, 50),
    )
    held = Unit(
        grade="A",
        unit="u-grain",
        sku="grain-1",
        category="grain",
        list_price=20_000,
        cost=12_000,
        supplied_at=NOW - 3600,
    )
    thin = inventory.model_copy(update={"units": (*inventory.units, held)})
    lines, _ = plan(thin, catalog, DEMAND, policy(), state, NOW, signal)
    assert any(str(line["sku"]).startswith("grain-") for line in lines)
    assert "grain-1" not in {str(line["sku"]) for line in lines if line["qty"] == 2}

    # 900 settles a day hold 5 grain SKUs; with five held, grain gets no new line.
    full = inventory.model_copy(
        update={
            "units": (
                *inventory.units,
                *(
                    held.model_copy(update={"unit": f"u-g{n}", "sku": f"grain-{n}"})
                    for n in range(1, 6)
                ),
            )
        }
    )
    catalog["grain-5"] = catalog["grain-4"].model_copy(update={"sku": "grain-5"})
    lines, _ = plan(full, catalog, DEMAND, policy(), state, NOW, signal)
    new_grain = [
        line
        for line in lines
        if line["qty"] == 2 and str(line["sku"]).startswith("grain-")
    ]
    assert not new_grain


def test_sold_out_sellers_are_rebought_before_new_skus() -> None:
    catalog, inventory, signal, state = fixture()
    catalog["juice-1"] = CatalogItem(
        sku="juice-1", category="juice", kind="food", list_price=9_000, shelf_hours=720
    )
    # juice-1 sold three times in the last hour and none is left on the shelf.
    state = state.model_copy(
        update={
            "last_sold_at": {**state.last_sold_at, "juice-1": NOW - 30},
            "sold_log": {**state.sold_log, "juice-1": [NOW - 30, NOW - 90, NOW - 150]},
        }
    )
    lines, _ = plan(inventory, catalog, DEMAND, policy(), state, NOW, signal)
    assert lines[0]["sku"] == "juice-1"


def test_grade_c_units_do_not_fill_a_best_sellers_depth() -> None:
    catalog = {
        "hot": CatalogItem(sku="hot", category="beds", kind="goods", list_price=600_000)
    }
    c_units = tuple(
        Unit(
            unit=f"c-{n}",
            sku="hot",
            category="beds",
            kind="goods",
            list_price=600_000,
            cost=200_000,
            grade="C",
            supplied_at=NOW - 3600,
        )
        for n in range(17)
    )
    inventory = Inventory(round=3, balance=100_000_000, units=c_units)
    signal = DemandSignal({"beds": 1_800}, {"hot": 1_800})
    lines, _ = plan(inventory, catalog, (), policy(), RestockState(), NOW, signal)
    assert any(line["sku"] == "hot" for line in lines)


def test_depth_gets_lines_before_new_skus() -> None:
    catalog, inventory, signal, state = fixture()
    lines, _ = plan(inventory, catalog, DEMAND, policy(), state, NOW, signal)
    skus = [str(line["sku"]) for line in lines]
    first_food = next(i for i, s in enumerate(skus) if not s.startswith("bed-"))
    # The depth SKU (the market's top bed) is planned before any new food SKU.
    assert any(s.startswith("bed-") for s in skus[:first_food])


def test_a_seller_down_to_its_last_units_is_rebought_first() -> None:
    catalog, inventory, signal, state = fixture()
    catalog["sauce-9"] = CatalogItem(
        sku="sauce-9", category="sauce", kind="food", list_price=16_500, shelf_hours=720
    )
    last = Unit(
        unit="u-last",
        sku="sauce-9",
        category="sauce",
        kind="food",
        list_price=16_500,
        cost=9_000,
        grade="A",
        supplied_at=NOW - 3600,
        expires_at=NOW + 600 * 3600,
    )
    inventory = inventory.model_copy(update={"units": (*inventory.units, last)})
    state = state.model_copy(
        update={
            "last_sold_at": {**state.last_sold_at, "sauce-9": NOW - 30},
            "sold_log": {**state.sold_log, "sauce-9": [NOW - 30, NOW - 90, NOW - 150]},
        }
    )
    lines, _ = plan(inventory, catalog, DEMAND, policy(), state, NOW, signal)
    assert lines[0]["sku"] == "sauce-9"
