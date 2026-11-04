"""Restock plans stay inside cash and risk bounds; reviews state certified facts."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from r3.market_files import (
    CatalogItem,
    DemandRow,
    read_catalog,
    read_demand,
    read_inventory,
)
from r3.policy import Policy, RestockPolicy
from r3.restock import (
    DemandSignal,
    RestockState,
    category_scores,
    plan,
    run,
    sku_fits,
    stock_budget,
)
from r3.review import run as review_run
from tests.r3_stock import CURTAIN, KIMCHI, LAMP, MILK, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path


def test_first_restock_spreads_long_life_food_and_mid_priced_goods_within_cash(
    tmp_path: Path,
) -> None:
    folder = write_market(tmp_path / ".market", [])
    now = time.time()
    lines, state = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        Policy().restock,
        RestockState(),
        now,
    )
    skus = {str(line["sku"]) for line in lines}
    # Then: 48 h milk and the 136,900 curtain are excluded; kimchi and the lamp fit.
    assert MILK not in skus
    assert CURTAIN not in skus  # 236,900 exceeds the goods price band
    assert {KIMCHI, LAMP} <= skus
    assert all(1 <= int(str(line["qty"])) <= 20 for line in lines)
    assert len(lines) <= 10
    assert state.first_run_at == now
    assert state.planned_cost <= 5_000_000 - Policy().restock.reserve_cash


def test_restock_replenishes_sold_units_and_stops_when_cash_is_at_the_reserve(
    tmp_path: Path,
) -> None:
    now = time.time()
    folder = write_market(
        tmp_path / ".market", [unit(KIMCHI, 1, now=now), unit(LAMP, 1, now=now)]
    )
    first, state = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        Policy().restock,
        RestockState(),
        now,
    )
    assert all(str(line["sku"]) not in {KIMCHI, LAMP} for line in first)
    # When: the kimchi unit disappears (sold) and the lamp stays.
    _ = write_market(folder, [unit(LAMP, 1, now=now)])
    second, state = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        Policy().restock,
        state,
        now + 60,
    )
    assert {"sku": KIMCHI, "qty": 1} in second
    # And: at the cash reserve nothing is bought.
    _ = write_market(
        folder, [unit(LAMP, 1, now=now)], balance=Policy().restock.reserve_cash
    )
    third, _ = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        Policy().restock,
        state,
        now + 120,
    )
    assert third == []


def test_restock_follows_settled_demand_and_round_two_buys_nothing(
    tmp_path: Path,
) -> None:
    now = time.time()
    folder = write_market(tmp_path / ".market", [])
    signal = DemandSignal({"가구/침구>조명>스탠드": 40}, {LAMP: 40})
    lines, _ = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        Policy().restock,
        RestockState(),
        now,
        signal=signal,
    )
    assert str(lines[0]["sku"]) == LAMP
    _ = write_market(folder, [], market_round=2)
    assert json.loads(run(folder, policy_file=tmp_path / "p.json", now=now)) == {
        "buy": []
    }


def test_restock_prints_nothing_while_the_supply_window_is_shut(
    tmp_path: Path,
) -> None:
    # Given: a Round 3 shop with cash and the market's supply window 10 min away.
    folder = tmp_path / ".market"
    now = time.time()
    shut = {"open": False, "opens_at": now + 600, "closes_at": now + 780}
    _ = write_market(folder, [], window=shut)
    # When / Then: the hook buys nothing (each line would be a lost 429).
    assert json.loads(run(folder, policy_file=tmp_path / "p.json", now=now)) == {
        "buy": []
    }
    # When: the window opens within the kit's retry wait, or is open.
    for window in (
        {**shut, "opens_at": now + 20},
        {"open": True, "opens_at": now - 30, "closes_at": now + 150},
    ):
        _ = write_market(folder, [], window=window)
        # Then: the plan goes out.
        assert json.loads(run(folder, policy_file=tmp_path / "p.json", now=now))["buy"]


def test_restock_never_buys_food_with_under_a_week_of_shelf_life() -> None:
    # Given: the strategy lowered the food shelf floor to 24 h.
    policy = Policy().restock.model_copy(update={"min_food_shelf_hours": 24})
    milk = CatalogItem(sku="M", kind="food", shelf_hours=48, list_price=20_000)
    kimchi = milk.model_copy(update={"sku": "K", "shelf_hours": 720})
    # Then: the owner's one-week floor still holds.
    assert not sku_fits(milk, policy)
    assert sku_fits(kimchi, policy)


def test_stock_budget_follows_equity_and_goods_rank_by_value() -> None:
    policy = Policy().restock.model_copy(
        update={"seed_budget": 2_000_000, "stock_share": 0.7}
    )
    # Then: 13M cash and 4M stock allow 11.9M of stock; a small shop keeps the floor.
    assert stock_budget(13_000_000, 4_000_000, policy) == 11_900_000
    assert stock_budget(1_000_000, 500_000, policy) == 2_000_000
    # And: with equal settles, the dear goods category ranks above the cheap one.
    catalog = {
        f"{name}{n}": CatalogItem(
            sku=f"{name}{n}", category=name, kind="goods", list_price=price
        )
        for name, price in (("bed", 420_000), ("mug", 20_000))
        for n in range(5)
    }
    demand = tuple(DemandRow(category=c, kind="goods") for c in ("mug", "bed"))
    signal = DemandSignal(by_category={"bed": 3, "mug": 3}, by_sku={})
    wide = policy.model_copy(
        update={"goods_min_list_price": 0, "goods_max_list_price": 1_000_000}
    )
    ranked = [row.category for _, row in category_scores(demand, catalog, signal, wide)]
    assert ranked == ["bed", "mug"]


def deal_file(tmp_path: Path, **settled: object) -> Path:
    folder = tmp_path / ".market"
    folder.mkdir(exist_ok=True)
    now = time.time()
    path = folder / "deal.json"
    _ = path.write_text(
        json.dumps(
            {
                "order": {
                    "order": "o-1",
                    "item": "김치",
                    "budget": 60000,
                    "acceptable": [KIMCHI],
                    "kind": "food",
                    "use_within_hours": 24,
                },
                "receipt": {
                    "shop": "Kimchi Shop",
                    "item": KIMCHI,
                    "price": 36000,
                    "task": "t",
                },
                "settled": {
                    "shop": "http://kimchi.test",
                    "unit": "u",
                    "sku": KIMCHI,
                    "name": "김치",
                    "grade": "A",
                    "claim": "A",
                    "claim_honest": True,
                    "expires_at": now + 600 * 3600,
                    "value": 52000,
                    "ledger": 4321,
                    **settled,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_review_rates_certified_facts_and_never_prints_the_budget(
    tmp_path: Path,
) -> None:
    honest = json.loads(review_run(deal_file(tmp_path)))
    assert honest["stars"] == 5
    assert "60000" not in honest["review"]
    assert "69%" in honest["review"]
    dishonest = json.loads(
        review_run(deal_file(tmp_path, claim="A", grade="C", claim_honest=False))
    )
    assert dishonest["stars"] == 1
    assert "certified C" in dishonest["review"]
    short = json.loads(
        review_run(deal_file(tmp_path, expires_at=time.time() + 5 * 3600))
    )
    assert short["stars"] <= 2
    recorded = (tmp_path / "r3-deals.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(recorded) == 3
    assert json.loads(recorded[0])["ledger"] == 4321


def test_restock_stocks_market_best_sellers_deep_and_skips_unsold_skus(
    tmp_path: Path,
) -> None:
    now = time.time()
    folder = write_market(
        tmp_path / ".market", [unit(CURTAIN, 1, now=now)], balance=30_000_000
    )
    policy = RestockPolicy(
        goods_min_list_price=0, goods_max_list_price=1_000_000, units_per_sku=10
    )
    # The curtain sells 800 a day market-wide, the lamp never has.
    signal = DemandSignal({}, {CURTAIN: 800})
    lines, _ = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        policy,
        RestockState(),
        now,
        signal,
    )
    by_sku = {line["sku"]: line["qty"] for line in lines}
    assert by_sku.get(CURTAIN) == 9  # depth min(10, 800 // 50), one already held
    assert LAMP not in by_sku
