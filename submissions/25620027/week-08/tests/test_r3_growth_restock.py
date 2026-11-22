"""Growth buys proven demand first and exposes only bounded capital to probes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

from r3.endgame import ROUND_END
from r3.growth import GrowthEvidence, SkuGrowth
from r3.market_files import CatalogItem, DemandRow, Inventory, SurgePrice, Unit
from r3.policy import RestockPolicy
from r3.restock import DemandSignal, RestockState, load_economics, plan

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

NOW: Final = ROUND_END - 10 * 86400


def item(sku: str, price: int = 400_000, category: str = "beds") -> CatalogItem:
    return CatalogItem(sku=sku, category=category, kind="goods", list_price=price)


def stock(product: CatalogItem, count: int) -> tuple[Unit, ...]:
    return tuple(
        Unit(
            grade="A",
            unit=f"{product.sku}-{n}",
            sku=product.sku,
            category=product.category,
            list_price=product.list_price,
            cost=product.list_price // 2,
            supplied_at=NOW - 3600,
        )
        for n in range(count)
    )


def profitable(
    sales: int = 9, exposure: float = 1.0, *, price: int = 400_000
) -> SkuGrowth:
    return SkuGrowth(
        sales_1h=sales,
        in_stock_hours=exposure,
        mature_sales=20,
        mature_net=price * 12 // 5,
        mature_cost=price * 12,
        median_hold_hours=1.0,
    )


def policy() -> RestockPolicy:
    return RestockPolicy(
        units_per_sku=20,
        depth_skus=12,
        goods_min_list_price=0,
        goods_max_list_price=5_000_000,
        replenish_qty=2,
        reserve_cash=0,
        stock_share=0.8,
        seed_budget=0,
        max_units=500,
    )


def categories(items: Sequence[CatalogItem]) -> tuple[DemandRow, ...]:
    return tuple(DemandRow(category=cat) for cat in {p.category for p in items})


def test_own_low_stock_precedes_twelve_market_depth_targets() -> None:
    # Given: profitable market leaders would otherwise consume all ten lines.
    own = item("own")
    products = [own, *(item(f"market-{n}", 1_000_000) for n in range(12))]
    signal = DemandSignal({}, {p.sku: 1000 for p in products[1:]} | {own.sku: 100})
    evidence = GrowthEvidence(
        {p.sku: profitable(0, price=p.list_price) for p in products[1:]}
        | {own.sku: profitable()}
    )
    inventory = Inventory(round=3, balance=300_000_000, units=stock(own, 1))
    # When: target generation merges market and actual own demand before spending.
    lines, _ = plan(
        inventory,
        {p.sku: p for p in products},
        categories(products),
        policy(),
        RestockState(),
        NOW,
        signal,
        growth=evidence,
    )
    # Then: own stock is topped to seven, with one unique line and kit-safe limits.
    assert lines[0] == {"sku": "own", "qty": 6}
    assert len(lines) == 10
    assert len({line["sku"] for line in lines}) == len(lines)
    assert all(1 <= int(str(line["qty"])) <= 20 for line in lines)


@pytest.mark.parametrize(("exposure", "quantity"), [(1.0, 6), (0.5, 12), (0.05, 6)])
def test_exposure_adjusts_rates_only_after_reliable_observation(
    exposure: float,
    quantity: int,
) -> None:
    # Given: nine actual sales and one remaining unit, with varying exposure.
    product = item("own")
    evidence = GrowthEvidence({product.sku: profitable(exposure=exposure)})
    inventory = Inventory(round=3, balance=30_000_000, units=stock(product, 1))
    # When: using own sales without any public market depth signal.
    lines, _ = plan(
        inventory,
        {product.sku: product},
        (),
        policy(),
        RestockState(),
        NOW,
        growth=evidence,
    )
    # Then: a three-minute listing never extrapolates to 180 sales/hour.
    assert lines == [{"sku": "own", "qty": quantity}]


def test_market_depth_and_own_depth_use_one_larger_target() -> None:
    # Given: market depth asks for 20 while own hourly pace asks for seven.
    product = item("own")
    inventory = Inventory(round=3, balance=30_000_000, units=stock(product, 1))
    # When: both signals are credible and the mature cohort is profitable.
    lines, _ = plan(
        inventory,
        {product.sku: product},
        (),
        policy(),
        RestockState(),
        NOW,
        DemandSignal({}, {product.sku: 1000}),
        growth=GrowthEvidence({product.sku: profitable()}),
    )
    # Then: buy 19, never two duplicate lines or an additive target of 27.
    assert lines == [{"sku": "own", "qty": 19}]


@pytest.mark.parametrize(
    ("mature_sales", "net", "expected"),
    [
        (20, -1, 0),
        (20, None, 1),
        (19, 1_000_000, 1),
        (20, 1_000_000, 20),
    ],
)
def test_mature_loss_blocks_rebuy_and_unknown_only_probes(
    mature_sales: int,
    net: int | None,
    expected: int,
) -> None:
    # Given: popular market SKU but differing completeness of our economics.
    product = item("new")
    row = SkuGrowth(
        mature_sales=mature_sales,
        mature_net=net,
        mature_cost=4_800_000,
        median_hold_hours=1,
    )
    # When: selecting stock with ample capital and a 20-unit market target.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000),
        {product.sku: product},
        categories([product]),
        policy(),
        RestockState(),
        NOW,
        DemandSignal({}, {product.sku: 1000}),
        growth=GrowthEvidence({product.sku: row}),
    )
    # Then: only mature positive evidence unlocks depth.
    assert lines == ([{"sku": "new", "qty": expected}] if expected else [])


def test_return_per_cost_hour_prioritizes_equal_coverage_under_scarce_cash() -> None:
    # Given: two equally urgent sellers with the same ROI but different hold times.
    slow, fast = item("slow", 100_000), item("fast", 100_000)
    evidence = GrowthEvidence(
        {
            "slow": SkuGrowth(
                sales_1h=9,
                in_stock_hours=1,
                mature_sales=20,
                mature_net=240_000,
                mature_cost=1_200_000,
                median_hold_hours=4,
            ),
            "fast": profitable(price=100_000),
        }
    )
    # When: the stock-share limit only funds three units.
    lines, _ = plan(
        Inventory(round=3, balance=300_000),
        {p.sku: p for p in [slow, fast]},
        (),
        policy(),
        RestockState(),
        NOW,
        growth=evidence,
    )
    # Then: the faster capital return gets the scarce allowance.
    assert lines == [{"sku": "fast", "qty": 3}]


def test_probes_fill_price_gaps_instead_of_duplicating_held_price() -> None:
    # Given: a held 400k unit, a nearby popular SKU, and an uncovered 900k band.
    held, near, gap = item("held"), item("near", 410_000), item("gap", 900_000)
    products = [held, near, gap]
    # When: evaluating exploration without a mature cohort.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000, units=stock(held, 1)),
        {p.sku: p for p in products},
        categories(products),
        policy(),
        RestockState(),
        NOW,
        DemandSignal({}, {"near": 1000, "gap": 5}),
        growth=GrowthEvidence({}),
    )
    # Then: even large nearby demand does not create a duplicate price-band probe.
    assert lines == [{"sku": "gap", "qty": 1}]


@pytest.mark.parametrize("quantity", [1, 3])
def test_unknown_price_band_probes_stay_small(quantity: int) -> None:
    # Given: distinct demanded prices and a deep global SKU limit.
    products = [
        item(str(n), price) for n, price in enumerate([100_000, 300_000, 900_000])
    ]
    settings = policy().model_copy(update={"probe_qty": quantity})
    # When: exploring three price points.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000),
        {p.sku: p for p in products},
        categories(products),
        settings,
        RestockState(),
        NOW,
        DemandSignal({}, {p.sku: 5 for p in products}),
        growth=GrowthEvidence({}),
    )
    # Then: units_per_sku=20 never makes an unproven lot larger than the probe limit.
    assert len(lines) == 3
    assert all(line["qty"] == quantity for line in lines)


def test_empty_public_counts_do_not_turn_category_metadata_into_demand() -> None:
    # Given: a category exists in demand.json but no market SKU has five settles.
    product = item("new")
    # When: evidence is unavailable but a real empty market signal is present.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000),
        {product.sku: product},
        categories([product]),
        policy(),
        RestockState(),
        NOW,
        DemandSignal({product.category: 10000}, {product.sku: 4}),
        growth=GrowthEvidence({}, available=False),
    )
    # Then: metadata and category counts cannot substitute for SKU demand.
    assert lines == []


def test_surge_and_refund_reserve_limit_the_same_remaining_cash() -> None:
    # Given: current 1.5x supply surcharge and refund reserve exceeding operations.
    product = item("own", 100_000)
    inventory = Inventory(
        round=3, balance=1_000_000, surge=(SurgePrice(sku="own", factor=1.5),)
    )
    settings = policy().model_copy(update={"reserve_cash": 200_000})
    evidence = GrowthEvidence(
        {
            "own": SkuGrowth(
                sales_1h=9,
                in_stock_hours=1,
                mature_sales=20,
                mature_cost=1_200_000,
                mature_net=800_000,
                median_hold_hours=1,
            )
        },
        refund_reserve=300_000,
    )
    # When: a deep demand target could otherwise spend all cash.
    lines, updated = plan(
        inventory,
        {product.sku: product},
        (),
        settings,
        RestockState(),
        NOW,
        DemandSignal({}, {"own": 1000}),
        growth=evidence,
    )
    # Then: max(200k,300k) is retained, not their sum or a second stock-share reserve.
    assert lines == [{"sku": "own", "qty": 6}]
    assert updated.planned_cost == 675_000


def test_growth_respects_remaining_global_capacity() -> None:
    # Given: only one global inventory slot remains despite high demand.
    product = item("own")
    inventory = Inventory(round=3, balance=30_000_000, units=stock(product, 2))
    # When: requesting deep replenishment.
    lines, _ = plan(
        inventory,
        {product.sku: product},
        (),
        policy().model_copy(update={"max_units": 3}),
        RestockState(),
        NOW,
        DemandSignal({}, {"own": 1000}),
        growth=GrowthEvidence({"own": profitable()}),
    )
    # Then: the plan fills exactly one slot.
    assert lines == [{"sku": "own", "qty": 1}]


def test_growth_cannot_override_the_endgame_stop() -> None:
    # Given: a profitable SKU with ample cash inside the last 2 hours.
    product = item("own")
    # When: the growth path sees demand during liquidation.
    lines, updated = plan(
        Inventory(round=3, balance=30_000_000),
        {"own": product},
        categories([product]),
        policy(),
        RestockState(),
        ROUND_END - 0.2 * 3600,  # 11/17: buying stops 0.25 h before the end
        DemandSignal({}, {"own": 1000}),
        growth=GrowthEvidence({"own": profitable()}),
    )
    # Then: there is no new buying or reserved cost.
    assert lines == []
    assert updated.planned_cost == 0


def test_disappearance_alone_cannot_create_growth_demand() -> None:
    # Given: a disappeared unit may have expired, and the ledger reports no sale.
    product = item("own")
    state = RestockState(units_seen={"own": ("old-unit",)})
    # When: growth evidence, rather than legacy disappearance counts, drives depth.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000),
        {"own": product},
        categories([product]),
        policy(),
        state,
        NOW,
        growth=GrowthEvidence({"own": profitable(0)}),
    )
    # Then: neither stock disappearance nor old profit invents current demand.
    assert lines == []


def test_unavailable_ledger_bootstrap_is_small_and_still_enforces_food_rules() -> None:
    # Given: no evidence at all, but both viable and too-short-lived food SKUs.
    short = CatalogItem(
        sku="short", category="milk", kind="food", list_price=50_000, shelf_hours=48
    )
    long = CatalogItem(
        sku="long", category="dry", kind="food", list_price=40_000, shelf_hours=168
    )
    second = long.model_copy(update={"sku": "second", "category": "dry2"})
    goods = item("goods")
    products = [short, long, second, goods]
    settings = policy().model_copy(
        update={"min_food_shelf_hours": 24, "food_share": 0.1}
    )
    # When: bootstrap fills uncovered categories without market data.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000),
        {p.sku: p for p in products},
        categories(products),
        settings,
        RestockState(),
        NOW,
        growth=GrowthEvidence({}, available=False),
    )
    # Then: exactly one safe food line and one goods line, never deep lots.
    assert "short" not in {line["sku"] for line in lines}
    assert len(lines) == 2
    assert all(line["qty"] == 1 for line in lines)
    assert any(line["sku"] == "goods" for line in lines)


def test_price_band_limit_includes_the_existing_held_point() -> None:
    # Given: one held price plus two unrepresented prices, limited to two bands.
    held, mid, high = item("held", 100_000), item("mid", 300_000), item("high", 900_000)
    products = [held, mid, high]
    # When: choosing market-supported probes with ample funds.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000, units=stock(held, 1)),
        {p.sku: p for p in products},
        categories(products),
        policy().model_copy(update={"price_bands": 2}),
        RestockState(),
        NOW,
        DemandSignal({}, {"mid": 5, "high": 5}),
        growth=GrowthEvidence({}),
    )
    # Then: only one additional price point fits the configured limit.
    assert lines == [{"sku": "high", "qty": 1}]


@pytest.mark.parametrize("net", [123.5, "invalid"])
def test_legacy_economics_boundary_preserves_valid_values_and_rejects_bad_rows(
    tmp_path: Path,
    net: float | str,
) -> None:
    # Given: the manager's actual wrapper and its per-SKU values.
    path = tmp_path / "economics.json"
    _ = path.write_text(
        json.dumps(
            {
                "ts": NOW,
                "skus": {
                    "sku": {"net_per_unit": net, "sold_12h": 4},
                },
            }
        )
    )
    # When: reading through the typed legacy boundary.
    result = load_economics(path)
    # Then: metadata is accepted, and invalid input fails closed to no legacy data.
    assert result == ({"sku": (123.5, 4)} if net == 123.5 else {})


def test_legacy_boost_and_merged_replenishment_never_exceed_twenty_per_line() -> None:
    # Given: both legacy targets can be boosted from 20 to 30 for the same SKU.
    product = item("own")
    state = RestockState(
        last_sold_at={"own": NOW - 60}, sold_log={"own": [NOW - 60] * 30}
    )
    # When: growth is omitted by an older caller, and the second pass merges depth.
    lines, _ = plan(
        Inventory(round=3, balance=30_000_000),
        {"own": product},
        (),
        policy(),
        state,
        NOW,
        DemandSignal({}, {"own": 1000}),
        economics={"own": (100_000.0, 24)},
    )
    # Then: the combined kit line still respects its hard 20-unit contract.
    assert lines == [{"sku": "own", "qty": 20}]


@pytest.mark.parametrize(
    ("surge", "cost", "net", "expected_qty"),
    [
        (1.0, 1_200_000, 200_000, 20),
        (1.5, 1_200_000, 200_000, 1),
        (1.5, 1_800_000, 200_000, 20),
        (1.5, 1_200_000, 570_000, 1),
    ],
)
def test_current_acquisition_cost_gates_deep_growth_without_resurging_old_cost(
    surge: float,
    cost: int,
    net: int,
    expected_qty: int,
) -> None:
    # Given: a historically profitable mature SKU and ample capital.
    product = item("own", 100_000)
    evidence = GrowthEvidence(
        {
            "own": SkuGrowth(
                mature_sales=20,
                mature_cost=cost,
                mature_net=net,
                median_hold_hours=1,
            )
        }
    )
    inventory = Inventory(
        round=3, balance=30_000_000, surge=(SurgePrice(sku="own", factor=surge),)
    )
    # When: the current acquisition forecast crosses retained receipts per unit.
    lines, _ = plan(
        inventory,
        {"own": product},
        categories([product]),
        policy(),
        RestockState(),
        NOW,
        DemandSignal({}, {"own": 1000}),
        growth=evidence,
    )
    # Then: no deep loss or breakeven lot; already-surged historical cost is not
    # multiplied again. A blocked depth target may still receive one bounded probe.
    assert lines == [{"sku": "own", "qty": expected_qty}]


def test_forecast_return_ranks_profitable_surge_below_better_current_return() -> None:
    # Given: historically higher ROI becomes thin but still positive after surge.
    high, steady = item("surged", 100_000), item("steady", 100_000)
    evidence = GrowthEvidence(
        {
            "surged": SkuGrowth(
                sales_1h=9,
                in_stock_hours=1,
                mature_sales=20,
                mature_cost=1_200_000,
                mature_net=600_000,
                median_hold_hours=1,
            ),
            "steady": profitable(price=100_000),
        }
    )
    inventory = Inventory(
        round=3, balance=1_000_000, surge=(SurgePrice(sku="surged", factor=1.5),)
    )
    # When: equal coverage competes for the same constrained stock budget.
    lines, _ = plan(
        inventory,
        {p.sku: p for p in [high, steady]},
        (),
        policy(),
        RestockState(),
        NOW,
        growth=evidence,
    )
    # Then: forecast ROI, not the historical higher ROI, wins the first allocation.
    assert lines == [{"sku": "steady", "qty": 7}, {"sku": "surged", "qty": 2}]
