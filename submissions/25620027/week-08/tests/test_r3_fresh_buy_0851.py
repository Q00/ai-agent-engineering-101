"""11/17 08:51 (owner): fresh food for buyer-02 and dyishappy, losing pairs rest.

Food that meets buyer-02's named need is bought first, fresh units go to two
forms, and a (shop, SKU) pair rests after six straight failed attempts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.buyer import LOSING_REST_MINUTES, LOSING_STREAK
from r3.events import EventLedger
from r3.market_files import CatalogItem, Inventory, Unit
from r3.policy import RestockPolicy
from r3.restock import FOOD_AB_LINE, DemandSignal, RestockState, plan
from r3.seller import Request, pick_unit
from tests.test_r3_fresh_list import NOW, SHELF, SKU, food

if TYPE_CHECKING:
    from pathlib import Path

POLICY = RestockPolicy(
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
ITEM = CatalogItem(
    sku=SKU, category="식품>생수", kind="food", list_price=5400, shelf_hours=SHELF
)


def buy(units: tuple[Unit, ...], needs: dict[str, float] | None) -> int:
    lines, _ = plan(
        Inventory(round=3, balance=900_000_000, units=units),
        {SKU: ITEM},
        (),
        POLICY,
        RestockState(),
        NOW,
        DemandSignal({}, {}),
        {SKU: (500.0, 60), **{f"o{i}": (9000.0, 10) for i in range(4)}},
        fresh_skus={SKU: 2.0},
        fresh_needs=needs,
    )
    return sum(int(str(r["qty"])) for r in lines if r["sku"] == SKU)


def test_buyer02_need_decides_whether_a_listed_sku_is_short() -> None:
    assert FOOD_AB_LINE == 300
    # 0.8 of 400 h left: fresh by ratio, but short of a 340 h need (+2 h margin).
    held = tuple(food(f"h{i}", 0.8 * SHELF) for i in range(3))
    assert buy(held, None) == 0
    assert buy(held, {SKU: 340.0}) == 20
    assert buy(held, {SKU: 300.0}) == 0


def test_fresh_food_goes_to_dyishappy_and_plain_forms_first() -> None:
    fresh = food("f", 0.9 * SHELF, cost=2000, grade="B")
    older = food("o", 0.5 * SHELF, cost=2000, grade="B")
    stock = (fresh, older)

    def picked(text: str) -> str | None:
        got = pick_unit(
            stock,
            Request((SKU,), None, text),
            NOW,
            set(),
            fresh_skus={SKU: 2.0},
            shelf_hours=lambda _sku: float(SHELF),
        )
        return got.unit if got else None

    assert picked(f"I want to buy {SKU}. Please quote a price in credits.") == "o"
    assert picked(f"I want to buy water ({SKU}). It must last 300 hours.") == "f"
    # The plain form (buyer-02 list SKU) still gets the fresh unit (fresh_choice).
    assert picked(f"I want to buy water ({SKU})") == "f"


def attempt(events: EventLedger, order: str, outcome: str) -> None:
    events.record_attempt(
        order=order,
        shop="https://slow.test/",
        shop_name="slow",
        sku="G1",
        list_price=10_000,
        price=None,
        outcome=outcome,  # type: ignore[arg-type]
        reason="",
    )


def test_six_straight_failures_rest_the_pair(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    assert (LOSING_STREAK, LOSING_REST_MINUTES) == (6, 60.0)
    for i in range(5):
        attempt(events, f"o{i}", "no_deal")
    assert events.losing_streak_pairs(LOSING_STREAK, LOSING_REST_MINUTES) == frozenset()
    attempt(events, "o5", "no_deal")
    rested = events.losing_streak_pairs(LOSING_STREAK, LOSING_REST_MINUTES)
    assert rested == frozenset({("https://slow.test", "G1")})
    attempt(events, "o6", "agreed")  # a deal clears it
    assert events.losing_streak_pairs(LOSING_STREAK, LOSING_REST_MINUTES) == frozenset()


def test_stale_stock_cost_no_longer_blocks_fresh_lines() -> None:
    from r3.restock import FRESH_MAX_COST  # noqa: PLC0415 - local to this test

    assert FRESH_MAX_COST == 30_000_000
    # 150 stale units at 100k cost = 15M (over the old 10M cap), none fresh.
    stale = tuple(food(f"s{i}", 0.4 * SHELF, cost=100_000) for i in range(150))
    assert buy(stale, None) == 20
