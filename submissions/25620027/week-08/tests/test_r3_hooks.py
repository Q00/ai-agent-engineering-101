"""Restock plans stay inside cash and risk bounds; reviews state certified facts."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from r3.market_files import read_catalog, read_demand, read_inventory
from r3.policy import Policy
from r3.restock import DemandSignal, RestockState, plan, run
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
