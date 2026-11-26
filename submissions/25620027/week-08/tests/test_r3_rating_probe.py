"""One explicit low-price trial line shares the unchanged kit/resource limits."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import JsonValue, TypeAdapter

from r3 import restock
from r3.endgame import ROUND_END
from r3.growth import GrowthEvidence, SkuGrowth
from r3.market_files import CatalogItem, DemandRow, Inventory, SurgePrice
from r3.policy import Policy, PolicyHistory, RestockPolicy
from tests.test_r3_growth_restock import profitable, stock

if TYPE_CHECKING:
    from pathlib import Path

NOW: Final = ROUND_END - 10 * 86400
CHEAP: Final = CatalogItem(sku="cheap", category="cheap", list_price=40000)
SECOND: Final = CatalogItem(sku="second", category="cheap", list_price=30000)
LEADERS: Final = tuple(
    CatalogItem(sku=f"leader-{n}", category="beds", list_price=1_000_000 - n * 1000)
    for n in range(10)
)
PRODUCTS: Final = {p.sku: p for p in (*LEADERS, CHEAP, SECOND)}
SIGNAL: Final = restock.DemandSignal({}, dict.fromkeys(PRODUCTS, 1000))


def policy(*, growth: bool = False) -> RestockPolicy:
    return RestockPolicy(
        growth_enabled=growth,
        probe_skus=(CHEAP.sku, SECOND.sku),
        goods_min_list_price=100000,
        goods_max_list_price=5_000_000,
        stock_share=0.8,
        seed_budget=0,
        reserve_cash=0,
        food_share=0,
        depth_skus=25,
        units_per_sku=20,
        max_units=500,
    )


def evidence() -> GrowthEvidence:
    return GrowthEvidence({p.sku: profitable(price=p.list_price) for p in LEADERS})


@pytest.mark.parametrize("growth", [False, True])
def test_one_rating_line_preserves_first_nine_leaders_and_records_tenth_cost(
    *, growth: bool
) -> None:
    inv = Inventory(round=3, balance=300_000_000)
    p = policy(growth=growth)
    baseline, _ = restock.plan(
        inv,
        PRODUCTS,
        (),
        p.model_copy(update={"probe_skus": ()}),
        restock.RestockState(),
        NOW,
        SIGNAL,
        growth=evidence(),
    )
    lines, state = restock.plan(
        inv, PRODUCTS, (), p, restock.RestockState(), NOW, SIGNAL, growth=evidence()
    )
    assert len(baseline) == len(lines) == 10
    assert lines[0] == {"sku": CHEAP.sku, "qty": 3}
    assert lines[1:] == baseline[:9]
    assert baseline[9]["sku"] not in {line["sku"] for line in lines}
    assert state.planned_cost == sum(
        int(str(line["qty"])) * int(PRODUCTS[str(line["sku"])].list_price * 0.75)
        for line in lines
    )


@pytest.mark.parametrize(
    ("a", "b", "sku", "qty"),
    [
        (0, 0, "cheap", 3),
        (2, 0, "second", 3),
        (3, 1, "second", 2),
        (2, 2, "cheap", 1),
        (3, 3, "", 0),
        (5, 3, "", 0),
    ],
)
def test_least_held_and_list_tie_order_with_shared_position_cap(
    a: int, b: int, sku: str, qty: int
) -> None:
    inv = Inventory(
        round=3, balance=300_000_000, units=stock(CHEAP, a) + stock(SECOND, b)
    )
    lines, _ = restock.plan(
        inv, PRODUCTS, (), policy(), restock.RestockState(), NOW, SIGNAL
    )
    cheap = [line for line in lines if line["sku"] in {CHEAP.sku, SECOND.sku}]
    assert cheap == ([{"sku": sku, "qty": qty}] if qty else [])
    assert len(lines) == 10


@pytest.mark.parametrize(
    "case",
    [
        "unnamed",
        "zero",
        "boundary",
        "above_min",
        "above_max",
        "food",
        "no_signal",
        "low_signal",
        "loss",
        "full",
        "reserve",
        "endgame",
    ],
)
def test_disqualified_rating_line_preserves_existing_plan(case: str) -> None:  # noqa: C901 - independent guard scenarios
    item = CHEAP
    p = policy().model_copy(update={"probe_skus": (CHEAP.sku,)})
    inv = Inventory(round=3, balance=300_000_000)
    signal = SIGNAL
    econ = {}
    now = NOW
    if case == "unnamed":
        p = p.model_copy(update={"probe_skus": ()})
    if case == "zero":
        item = item.model_copy(update={"list_price": 0})
    if case == "boundary":
        item = item.model_copy(update={"list_price": 50000})
    if case == "above_min":
        p = p.model_copy(update={"goods_min_list_price": 40000})
    if case == "above_max":
        p = p.model_copy(update={"goods_max_list_price": 39999})
    if case == "food":
        item = item.model_copy(update={"kind": "food", "shelf_hours": 24})
    if case == "no_signal":
        signal = None
    if case == "low_signal":
        signal = restock.DemandSignal({}, {**SIGNAL.by_sku, CHEAP.sku: 4})
    if case == "loss":
        econ = {CHEAP.sku: (-1.0, 3)}
    if case == "full":
        inv = inv.model_copy(update={"units": stock(LEADERS[0], 500)})
    if case == "reserve":
        p = p.model_copy(update={"reserve_cash": inv.balance})
    if case == "endgame":
        now = ROUND_END - 720  # 11/17: buying stops 0.25 h before the end
    products = {**PRODUCTS, CHEAP.sku: item}
    lines, state = restock.plan(
        inv, products, (), p, restock.RestockState(), now, signal, econ
    )
    baseline, old = restock.plan(
        inv,
        products,
        (),
        p.model_copy(update={"probe_skus": ()}),
        restock.RestockState(),
        now,
        signal,
        econ,
    )
    assert lines == baseline
    assert state == old


def test_affordable_limits_shared_cash_stock_share_total_and_surge() -> None:
    p = policy().model_copy(update={"probe_skus": (CHEAP.sku,)})
    inv = Inventory(
        round=3, balance=100000, surge=(SurgePrice(sku=CHEAP.sku, factor=2),)
    )
    lines, state = restock.plan(
        inv, {CHEAP.sku: CHEAP}, (), p, restock.RestockState(), NOW, SIGNAL
    )
    assert lines == [{"sku": CHEAP.sku, "qty": 1}]
    assert state.planned_cost == 60000
    inv = Inventory(round=3, balance=300_000_000, units=stock(SECOND, 499))
    lines, _ = restock.plan(
        inv, {CHEAP.sku: CHEAP}, (), p, restock.RestockState(), NOW, SIGNAL
    )
    assert lines == [{"sku": CHEAP.sku, "qty": 1}]


@pytest.mark.parametrize("growth", [False, True])
def test_production_run_uses_one_line_without_filter_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, growth: bool
) -> None:
    folder = tmp_path / "market"
    folder.mkdir()
    _ = (folder / "inventory.json").write_text(
        Inventory(round=3, balance=300_000_000).model_dump_json()
    )
    _ = (folder / "catalog.json").write_text(
        json.dumps({k: v.model_dump() for k, v in PRODUCTS.items()})
    )
    _ = (folder / "demand.json").write_text(
        json.dumps([DemandRow(category="beds").model_dump()])
    )
    pf = tmp_path / "policy.json"
    _ = pf.write_text(
        PolicyHistory(current=Policy(restock=policy(growth=growth))).model_dump_json()
    )

    def frozen_growth(*_: object) -> GrowthEvidence:
        return evidence()

    monkeypatch.setattr(restock, "build_growth", frozen_growth)
    result = TypeAdapter(dict[str, JsonValue]).validate_json(
        restock.run(folder, policy_file=pf, signal=SIGNAL, now=NOW)
    )
    assert isinstance(result["buy"], list)
    assert result["buy"][0] == {"sku": CHEAP.sku, "qty": 3}
    assert len(result["buy"]) == 10
    assert not restock.sku_fits(CHEAP, policy(growth=growth))


@pytest.mark.parametrize("case", ["unavailable", "mature_loss", "only_room_below_one"])
def test_growth_and_affordability_can_refuse_pilot_without_consuming_line(
    case: str,
) -> None:
    p = policy(growth=True).model_copy(update={"probe_skus": (CHEAP.sku,)})
    inv = Inventory(round=3, balance=300_000_000)
    ev = evidence()
    if case == "unavailable":
        ev = GrowthEvidence({}, available=False)
    if case == "mature_loss":
        ev = GrowthEvidence(
            {
                **ev.by_sku,
                CHEAP.sku: SkuGrowth(
                    mature_sales=20, mature_net=-1, mature_cost=600000
                ),
            }
        )
    if case == "only_room_below_one":
        inv = inv.model_copy(update={"balance": 30000})
    lines, state = restock.plan(
        inv, PRODUCTS, (), p, restock.RestockState(), NOW, SIGNAL, growth=ev
    )
    baseline, previous = restock.plan(
        inv,
        PRODUCTS,
        (),
        p.model_copy(update={"probe_skus": ()}),
        restock.RestockState(),
        NOW,
        SIGNAL,
        growth=ev,
    )
    assert lines == baseline
    assert state == previous


def test_only_first_two_eligible_named_candidates_are_allocated() -> None:
    third = CHEAP.model_copy(update={"sku": "third"})
    p = policy().model_copy(update={"probe_skus": (CHEAP.sku, SECOND.sku, third.sku)})
    inv = Inventory(
        round=3, balance=300_000_000, units=stock(CHEAP, 3) + stock(SECOND, 3)
    )
    signal = restock.DemandSignal({}, {**SIGNAL.by_sku, third.sku: 1000})
    lines, _ = restock.plan(
        inv, {**PRODUCTS, third.sku: third}, (), p, restock.RestockState(), NOW, signal
    )
    assert len(lines) == 10
    assert all(str(line["sku"]).startswith("leader-") for line in lines)
