"""buyer-02 list SKUs: fresh food kept for the plain form, restock tops it up."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from r3.endgame import ROUND_END
from r3.fresh_list import (
    find_buyer02_needs,
    find_fresh_list,
    load_fresh_list,
    write_fresh_list,
)
from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import DemandSignal, RestockState, plan
from r3.seller import Request, fresh_choice, pick_unit

if TYPE_CHECKING:
    from pathlib import Path

NOW = ROUND_END - 60 * 3600
SKU = "G7fa48eaa"
SHELF = 400  # hours of shelf life in the catalog
OURS = "https://ours.test"


def food(name: str, hours_left: float, cost: int = 2000, grade: str = "B") -> Unit:
    return Unit(
        unit=name,
        sku=SKU,
        category="식품>생수",
        kind="food",
        list_price=5400,
        cost=cost,
        grade=grade,
        supplied_at=NOW - 3600,
        expires_at=NOW + hours_left * 3600,
    )


FRESH_DEAR = food("fd", 0.9 * SHELF, cost=2600, grade="A")
FRESH_CHEAP = food("fc", 0.8 * SHELF, cost=2200, grade="A")
FAIR_LONG = food("fl", 0.7 * SHELF)
FAIR_SHORT = food("fs", 0.55 * SHELF)
STALE = food("st", 0.3 * SHELF, cost=1500, grade="C")
PLAIN = f"I want to buy water ({SKU})"
LISTED = {SKU: 6.75}


def shelf(_sku: str) -> float:
    return float(SHELF)


def pick(units: tuple[Unit, ...], text: str) -> Unit | None:
    return pick_unit(
        units,
        Request((SKU,), None, text),
        NOW,
        set(),
        fresh_skus=LISTED,
        shelf_hours=shelf,
    )


def test_plain_form_gets_a_then_b_then_none() -> None:
    # (a) the cheapest unit with 0.75+ of its life left...
    assert pick((FRESH_DEAR, FRESH_CHEAP, FAIR_LONG, STALE), PLAIN) is FRESH_CHEAP
    # (b) ...else the longest-lived 0.5+ unit...
    assert pick((FAIR_SHORT, FAIR_LONG, STALE), PLAIN) is FAIR_LONG
    # (c) ...else the longest-lived unit (11/15 23:5x; was none in stock).
    assert pick((STALE,), PLAIN) is STALE
    assert fresh_choice([STALE], NOW, shelf) is STALE


def test_other_openings_get_the_shortest_life_that_fits() -> None:
    other = f"I want to buy {SKU}"
    assert pick((FRESH_CHEAP, FAIR_SHORT, FAIR_LONG), other) is FAIR_SHORT
    # A stated need is still met.
    need = f"I want to buy water ({SKU}). Must last at least {0.6 * SHELF} hours."
    assert pick((FAIR_SHORT, FAIR_LONG), need) is FAIR_LONG


def test_unlisted_sku_keeps_the_old_order() -> None:
    units = (FAIR_LONG, STALE)
    plain = Request((SKU,), None, PLAIN)
    assert pick_unit(units, plain, NOW, set()) is pick_unit(
        units, plain, NOW, set(), fresh_skus={}, shelf_hours=shelf
    )


def test_list_needs_buyer_02_share_and_volume(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    rows = (
        [("buyer-02", SKU)] * 12
        + [("usagi", SKU)] * 2
        + [("buyer-02", "Gother")] * 12
        + [("usagi", "Gother")] * 12
        + [("buyer-02", "Gfew")] * 4
    )
    with closing(sqlite3.connect(path)) as db, db:
        for seq, (buyer, sku) in enumerate(rows):
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, buyer, shop, sku, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    seq,
                    NOW - 600,
                    "settle",
                    buyer,
                    OURS,
                    sku,
                    json.dumps(
                        {
                            "expires_at": NOW + 99_999,
                        }
                    ),
                ),
            )
    listed = find_fresh_list(path, OURS, NOW)
    # Then: only the SKU where buyer-02/01 make 70 %+ with 5+ buyer-02 settles.
    assert listed == {SKU: 0.5}
    write_fresh_list(tmp_path / "list.json", listed, NOW)
    assert load_fresh_list(tmp_path / "list.json", NOW) == listed
    assert load_fresh_list(tmp_path / "list.json", NOW + 3 * 3600) == {}


def test_restock_tops_up_fresh_stock_up_to_the_safety_caps() -> None:
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        max_units=3000,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        food_share=1.0,
        min_food_shelf_hours=168,
    )
    item = CatalogItem(
        sku=SKU, category="식품>생수", kind="food", list_price=5400, shelf_hours=SHELF
    )
    stale = tuple(food(f"s{i}", 0.4 * SHELF) for i in range(5))
    econ = {SKU: (500.0, 60), **{f"o{i}": (9000.0, 10) for i in range(4)}}

    def buy(units: tuple[Unit, ...], listed: dict[str, float]) -> int:
        lines, _ = plan(
            Inventory(round=3, balance=900_000_000, units=units),
            {SKU: item},
            (),
            policy,
            RestockState(),
            NOW,
            DemandSignal({}, {}),
            econ,
            fresh_skus=listed,
        )
        return sum(int(str(r["qty"])) for r in lines if r["sku"] == SKU)

    # Given: 5 stale units, no fresh one, buyer-02 buys 2 an hour.
    # Then (11/15 16:47): a full 20-unit line, whatever the total held.
    assert buy(stale, {SKU: 2.0}) == 20
    many = tuple(food(f"m{i}", 0.4 * SHELF) for i in range(150))
    assert buy(many, {SKU: 2.0}) == 20
    # A SKU at FRESH_MAX_HELD units stops.
    full = tuple(food(f"x{i}", 0.4 * SHELF) for i in range(200))
    assert buy(full, {SKU: 2.0}) == 0
    # Not listed: nothing (no recent sale in the restock state either).
    assert buy(stale, {}) == 0
    # Enough fresh units on hand: nothing more.
    fresh = (food("f1", 0.9 * SHELF), food("f2", 0.85 * SHELF))
    assert buy(stale + fresh, {SKU: 2.0}) == 0


def test_two_low_ratings_from_buyer_02_list_a_sku(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    with closing(sqlite3.connect(path)) as db, db:
        for n in range(2):
            order = f"o-{n}"
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, buyer, shop, sku, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    10 + n,
                    NOW - 900,
                    "settle",
                    "buyer-02",
                    OURS,
                    "Glow",
                    json.dumps({"expires_at": NOW + 9_999, "order": order}),
                ),
            )
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, buyer, shop, sku, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    20 + n,
                    NOW - 600,
                    "result",
                    "buyer-02",
                    OURS,
                    "",
                    json.dumps({"order": order, "stars": 2, "verified": True}),
                ),
            )
    # Two buyer-02 settles (under LIST_MIN) but two 2-star ratings: listed.
    assert "Glow" in find_fresh_list(path, OURS, NOW)


def test_fresh_list_keeps_two_lines_ahead_of_rater_lines() -> None:
    policy = RestockPolicy(
        growth_enabled=False,
        units_per_sku=20,
        max_units=3000,
        depth_skus=1,
        seed_budget=0,
        reserve_cash=0,
        stock_share=0.8,
        food_share=0.6,
        min_food_shelf_hours=168,
    )
    listed = [f"G7fa48ea{i}" for i in range(3)]
    raters = [f"G1111111{i}" for i in range(6)]
    catalog = {
        sku: CatalogItem(
            sku=sku,
            category="식품>생수",
            kind="food",
            list_price=5400,
            shelf_hours=SHELF,
        )
        for sku in listed + raters
    }
    stale = tuple(
        food(f"{sku}-{i}", 0.4 * SHELF).model_copy(update={"sku": sku})
        for sku in listed
        for i in range(3)
    )
    econ = dict.fromkeys(listed + raters, (500.0, 30))
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=stale),
        catalog,
        (),
        policy,
        RestockState(),
        NOW,
        DemandSignal({}, {}),
        econ,
        rater_skus=tuple(raters),
        fresh_skus=dict.fromkeys(listed, 2.0),
    )
    # Then: with 6 food lines, at least two go to the short fresh-list SKUs.
    assert sum(1 for line in lines if line["sku"] in listed) >= 2


def test_buyer02_needs_take_the_largest_named_need(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    _ = LedgerDB(path)
    rows = []
    for n, need in enumerate((84.0, 126.0)):
        order = f"o-{n}"
        rows.append(
            ("settle", "buyer-02", SKU, {"expires_at": NOW + 9_999, "order": order})
        )
        rows.append(
            (
                "result",
                "buyer-02",
                "",
                {
                    "order": order,
                    "stars": 2,
                    "verified": True,
                    "review": f"Only 50.0 h of shelf life left; I needed {need} h.",
                },
            )
        )
    with closing(sqlite3.connect(path)) as db, db:
        for seq, (kind, buyer, sku, data) in enumerate(rows):
            _ = db.execute(
                "INSERT INTO rows (seq, ts, kind, buyer, shop, sku, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (seq, NOW - 600, kind, buyer, OURS, sku, json.dumps(data)),
            )
    assert find_buyer02_needs(path, OURS, NOW) == {SKU: 126.0}
