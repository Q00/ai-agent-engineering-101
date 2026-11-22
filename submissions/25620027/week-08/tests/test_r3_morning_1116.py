"""11/16 morning rules: dyishappy A first, buyer-02 kimchi, buyer stats per kind."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING

from r3.events import KIND_MIN, EventLedger, blend_counts, blend_value
from r3.market_files import Unit
from r3.seller import (
    Quote,
    Request,
    a_first_buyer,
    dyishappy_quote,
    offerable_units,
    pick_unit,
)
from tests.r3_payments import paid

if TYPE_CHECKING:
    from pathlib import Path

NOW = time.time()
KIMCHI = "G7e48d0d9"
SHELF = 168.0


def food(name: str, sku: str, ratio: float, grade: str, cost: int = 2000) -> Unit:
    return Unit(
        unit=name,
        sku=sku,
        category="식품>김치",
        kind="food",
        list_price=5400,
        cost=cost,
        grade=grade,
        supplied_at=NOW - 3600,
        expires_at=NOW + ratio * SHELF * 3600,
    )


def goods(name: str, grade: str, cost: int) -> Unit:
    return Unit(
        unit=name,
        sku="Gabcdef01",
        category="가구>의자",
        kind="goods",
        list_price=100_000,
        cost=cost,
        grade=grade,
        supplied_at=NOW - 3600,
    )


def shelf(_sku: str) -> float:
    return SHELF


def pick(
    units: tuple[Unit, ...], text: str, skus: tuple[str, ...], **kw: bool
) -> Unit | None:
    offered = offerable_units(units, text)
    return pick_unit(
        offered, Request(skus, None, text), NOW, set(), shelf_hours=shelf, **kw
    )


def test_dyishappy_rule_is_off_since_10_3x() -> None:
    # 11/16 10:3x: 36 new ratings averaged 3.14 (< 3.3), so the form is served as
    # before 09:00: no A-first, C allowed.
    text = "I want to buy a Gabcdef01"
    assert not a_first_buyer(text)
    c = goods("c", "C", 20_000)
    assert pick((c,), text, ("Gabcdef01",)) is c


def test_plain_form_needs_buyer02_named_need_plus_margin() -> None:
    # 11/17 07:2x: replaced the two-kimchi rule. buyer-02 named 126 h for this SKU.
    plain = f"I want to buy 김치 ({KIMCHI})"
    long = food("l", KIMCHI, 0.9, "A", cost=2600)  # 151 h left
    short = food("s", KIMCHI, 0.75, "B", cost=1500)  # 126 h left (< 128)
    needs = {KIMCHI: 126.0}
    units = offerable_units((short, long), plain)
    req = Request((KIMCHI,), None, plain)
    assert (
        pick_unit(units, req, NOW, set(), shelf_hours=shelf, plain_needs=needs) is long
    )
    only_short = offerable_units((short,), plain)
    assert pick_unit(only_short, req, NOW, set(), plain_needs=needs) is None
    # Without a named need the plain form sells as before.
    assert pick_unit(only_short, req, NOW, set(), plain_needs={}) is short


def test_stale_kimchi_goes_to_q00_and_stated_needs_first() -> None:
    fair = food("m", KIMCHI, 0.6, "B", cost=1000)
    stale = food("s", KIMCHI, 0.45, "B", cost=1500)
    q00 = "I want to buy a 김치 (buyer: q00) within budget 9000"
    assert pick((fair, stale), q00, (), cheapest=True) is stale
    need = f"I want to buy 김치 ({KIMCHI}), it must last at least 50.0 hours"
    assert pick((fair, stale), need, (KIMCHI,)) is stale
    # A need the stale unit cannot meet still gets the fair one.
    longer = f"I want to buy 김치 ({KIMCHI}), it must last at least 90.0 hours"
    assert pick((fair, stale), longer, (KIMCHI,)) is fair


def test_blend_uses_own_kind_once_samples_suffice() -> None:
    mine = {"paid": 0, "no_deal": KIND_MIN}
    every = {"paid": 22, "no_deal": KIND_MIN}
    assert blend_counts(mine, every, KIND_MIN) == {"no_deal": KIND_MIN}
    assert blend_counts({}, every, 0) == every
    assert blend_value(0.5, 0.7, KIND_MIN // 2) == 0.6
    assert blend_value(None, 0.7, 0) == 0.7


def test_shop_outcomes_split_food_and_goods(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    shop = "https://psy.test"
    with closing(sqlite3.connect(events.path)) as db, db:
        for i in range(12):
            _ = db.execute(
                "INSERT INTO buyer_orders VALUES (?,?,?,?,?,?)",
                (f"f{i}", NOW, 9000, "food", NOW + 60, 1),
            )
            _ = db.execute(
                "INSERT INTO buyer_orders VALUES (?,?,?,?,?,?)",
                (f"g{i}", NOW, 90000, "goods", NOW + 60, 1),
            )
    for i in range(12):
        events.record_attempt(
            order=f"f{i}",
            shop=shop,
            shop_name="psy",
            sku="Gf",
            list_price=5000,
            price=4000,
            outcome="agreed",
        )
        paid(events, f"f{i}", shop, "Gf", ok=True)
        events.record_attempt(
            order=f"g{i}",
            shop=shop,
            shop_name="psy",
            sku="Gg",
            list_price=90000,
            price=None,
            outcome="no_deal",
        )
    every = events.shop_outcomes(1.0)[shop]
    assert every["paid"] == 12
    assert every["no_deal"] == 12
    goods_view = events.shop_outcomes(1.0, "goods")[shop]
    assert goods_view.get("paid", 0) == 0
    assert goods_view["no_deal"] == 12
    food_view = events.shop_outcomes(1.0, "food")[shop]
    assert food_view["paid"] == 12
    assert "no_deal" not in food_view
    assert events.time_yield(1.0, "goods")["psy"][1] == 0
    assert events.time_yield(1.0, "food")["psy"][1] == 12


def test_fair_furniture_form_gets_a_first_then_b_never_c() -> None:
    text = "I want to buy Gabcdef01. Please quote a price in credits."
    assert a_first_buyer(text)
    a, b, c = goods("a", "A", 60_000), goods("b", "B", 40_000), goods("c", "C", 20_000)
    assert pick((a, b, c), text, ("Gabcdef01",)) is a
    assert pick((b, c), text, ("Gabcdef01",)) is b
    assert pick((c,), text, ("Gabcdef01",)) is None
    fa = food("fa", KIMCHI, 0.9, "A", cost=2600)
    fb = food("fb", KIMCHI, 0.6, "B", cost=1500)
    food_text = f"I want to buy {KIMCHI}. Please quote a price in credits."
    # 11/17 08:2x: grade A food is kept for the dyishappy form; this form gets B.
    assert pick((fb, fa), food_text, (KIMCHI,)) is fb
    assert pick((fa,), food_text, (KIMCHI,)) is fa


def test_period_form_gets_the_cheapest_a_or_b_unit() -> None:
    text = "I want to buy pad (Gabcdef01)."
    a, b, c = goods("a", "A", 60_000), goods("b", "B", 40_000), goods("c", "C", 20_000)
    assert pick((a, b, c), text, ("Gabcdef01",)) is b
    assert pick((a, c), text, ("Gabcdef01",)) is a
    # Without A or B the usual order still applies (C only when nothing else).
    assert pick((c,), text, ("Gabcdef01",)) is c


def test_dyishappy_a_is_priced_at_its_star_shares() -> None:
    text = "I want to buy a G7e48d0d9"
    cheap = food("c", KIMCHI, 0.9, "A", cost=3000)  # 0.556 of 5400
    mid = food("m", KIMCHI, 0.9, "A", cost=3400)  # 0.630
    dear = food("d", KIMCHI, 0.9, "A", cost=3600)  # 0.667
    q = Quote(5000, 4500, 4000, "A", clearance=False, stale=False)
    assert dyishappy_quote(cheap, q, text).accept_at == 3159  # 0.585 x 5400
    assert dyishappy_quote(mid, q, text).accept_at == 3510  # 0.65 x 5400
    assert dyishappy_quote(dear, q, text) is q
    assert dyishappy_quote(cheap, q, "I want to buy 김치 (G7e48d0d9)") is q
    # The A unit with the lowest cost share goes first.
    picked = pick((dear, mid, cheap), text, ("G7e48d0d9",))
    assert picked is cheap
