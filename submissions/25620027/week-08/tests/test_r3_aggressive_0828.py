"""11/17 08:28 (owner, last day): wider market-first buying and dyishappy A food.

Every passing SKU, speed-based holds, fast-seller accept lines, grade A food kept
for the dyishappy form, and its approved under-cost exception with a loss cap.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from r3.market_files import Inventory, Unit
from r3.restock import (
    FAST_HOLD_MAX,
    load_fast_sellers,
    market_first_lines,
    write_fast_sellers,
)
from r3.seller import INEXTRO_SINCE, Quote, Request, fast_quote, pick_unit
from tests.r3_stock import LAMP, MILK, unit
from tests.test_r3_book_defence_0807 import NOW, goods, ledger

if TYPE_CHECKING:
    from pathlib import Path


def lamp_units(sku: str, n: int) -> tuple[Unit, ...]:
    return tuple(
        Unit.model_validate(unit(LAMP, i + 1, now=NOW)).model_copy(
            update={"sku": sku, "unit": f"{sku}-{i}"}
        )
        for i in range(n)
    )


def test_fast_sellers_hold_twice_market_rate_and_idle_ones_stop(
    tmp_path: Path,
) -> None:
    # Both sell 25 an hour in the market. fast: 4 held, 6 sold in the hour (60 %);
    # idle: 4 held, none sold in the hour.
    path = ledger(tmp_path, {"fast": (150, 120), "idle": (150, 120)})
    inv = Inventory(
        round=3,
        balance=10**9,
        units=lamp_units("fast", 4) + lamp_units("idle", 4),
    )
    sold = {"fast": [NOW - 600] * 6, "idle": [NOW - 7200] * 3}
    out: dict[str, float] = {}
    lines = market_first_lines(path, NOW, goods("fast", "idle"), inv, sold, out)
    assert lines == [{"sku": "fast", "qty": 20}]  # hold 50, 20 a run
    assert out == {"fast": 120.0}
    assert FAST_HOLD_MAX == 60


def test_fast_seller_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "fast.json"
    write_fast_sellers(path, {"G1": 1000.0}, 100.0)
    assert load_fast_sellers(path, 200.0) == {"G1": 1000.0}
    assert load_fast_sellers(path, 100.0 + 3 * 3600) == {}


def test_fast_quote_raises_todays_accept_line_toward_the_median() -> None:
    lamp = unit(LAMP, 1, grade="A", cost=100_000, now=INEXTRO_SINCE + 7200)
    today = Unit.model_validate(lamp)
    priced = Quote(130_000, 106_000, 102_000, "A", clearance=False, stale=False)
    # min(median x 0.98 = 112,700, cost x 1.15 = 115,000) -> 112,700
    got = fast_quote(today, priced, 115_000.0)
    assert (got.ask, got.accept_at, got.floor) == (130_000, 112_700, 102_000)
    # A low median still keeps cost x 1.08; a higher accept line is kept.
    assert fast_quote(today, priced, 100_000.0).accept_at == 108_000
    high = Quote(130_000, 120_000, 102_000, "A", clearance=False, stale=False)
    assert fast_quote(today, high, 115_000.0) is high
    old = today.model_copy(update={"supplied_at": INEXTRO_SINCE - 60})
    assert fast_quote(old, priced, 115_000.0) is priced
    assert fast_quote(today, priced, None) is priced


def test_fast_quote_leaves_grade_b_to_the_b_floor() -> None:
    lamp = unit(LAMP, 1, grade="B", cost=100_000, now=INEXTRO_SINCE + 7200)
    today = Unit.model_validate(lamp)
    priced = Quote(130_000, 106_000, 102_000, "B", clearance=False, stale=False)
    assert fast_quote(today, priced, 115_000.0) is priced


def test_grade_a_food_is_kept_for_the_dyishappy_form() -> None:
    now = time.time()
    food_a = Unit.model_validate(unit(MILK, 1, grade="A", cost=900, now=now))
    food_b = Unit.model_validate(unit(MILK, 2, grade="B", cost=1_000, now=now))
    stock = (food_a, food_b)

    def picked(text: str) -> Unit | None:
        return pick_unit(stock, Request((MILK,), None, text), now, set())

    assert picked(f"I want to buy a {MILK}") is food_a  # dyishappy form
    assert picked(f"I want to buy {MILK}. Please quote a price in credits.") is food_b
    assert picked(f"I want to buy milk ({MILK}). I need grade A.") is food_a
    assert picked(f"I want to buy milk ({MILK})") is food_a  # plain form untouched
    assert pick_unit((food_a,), Request((MILK,), None, "milk"), now, set()) is food_a


def test_dyishappy_food_a_under_cost_exception_and_loss_cap(tmp_path: Path) -> None:
    from r3.seller import (  # noqa: PLC0415 - local to this test
        DYISHAPPY_LOSS_CAP,
        add_dyishappy_loss,
        dyishappy_quote,
        load_dyishappy_loss,
    )

    now = time.time()
    text = f"I want to buy a {MILK}"
    # list 10,000 -> 5,850; cost 6,500: 5,850 >= 6,500 x 0.85 = 5,525 -> sold under cost
    row = unit(MILK, 1, grade="A", cost=6_500, now=now)
    food_a = Unit.model_validate(row).model_copy(update={"list_price": 10_000})
    usual = Quote(7_500, 7_100, 7_000, "A", clearance=False, stale=False)
    got = dyishappy_quote(food_a, usual, text, 0)
    assert (got.ask, got.accept_at, got.floor) == (5_850, 5_850, 5_850)
    # Too dear (5,850 < cost x 0.85), at the cap, or another form: no exception.
    dear = food_a.model_copy(update={"cost": 7_000})
    assert dyishappy_quote(dear, usual, text, 0).accept_at >= dear.cost
    assert dyishappy_quote(food_a, usual, text, DYISHAPPY_LOSS_CAP) is usual
    assert dyishappy_quote(food_a, usual, f"I want to buy milk ({MILK})", 0) is usual
    lamp = Unit.model_validate(unit(LAMP, 1, grade="A", cost=6_500, now=now))
    lamp = lamp.model_copy(update={"list_price": 10_000})
    assert dyishappy_quote(lamp, usual, f"I want to buy a {LAMP}", 0).accept_at >= 6_500
    path = tmp_path / "loss.json"
    assert load_dyishappy_loss(path) == 0
    assert add_dyishappy_loss(path, 650, now) == 650
    assert add_dyishappy_loss(path, 1_000, now) == 1_650
    assert load_dyishappy_loss(path) == 1_650
