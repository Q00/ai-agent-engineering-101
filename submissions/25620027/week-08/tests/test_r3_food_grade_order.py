"""Lower-grade buyers get food B or C first; others keep A/B before C."""

from __future__ import annotations

from r3.market_files import Unit
from r3.seller import Request, low_grade_order, offerable_units, pick_unit

NOW = 1_794_560_000.0
SKU = "G0000000f"


def food(name: str, grade: str, hours_left: float, cost: int = 5000) -> Unit:
    return Unit(
        unit=name,
        sku=SKU,
        category="식품>김치/반찬>김치",
        kind="food",
        list_price=10000,
        cost=cost,
        grade=grade,
        supplied_at=NOW - 3600,
        expires_at=NOW + hours_left * 3600,
    )


A_FRESH = food("a", "A", 600)
B_MID = food("b", "B", 400)
C_OLD = food("c", "C", 300, cost=4000)
STOCK = (A_FRESH, B_MID, C_OLD)
DAE_YANGKIM = (
    f"I want to buy kimchi ({SKU}). I need at least 100 hours of shelf life left. "
    "Grade B or C is fine if it is cheaper; please quote your lowest-priced unit."
)
KIMAUVE = f"I want to buy kimchi ({SKU}), it must last 100 hours"
PSYOUNG = (
    f"I want to buy kimchi ({SKU}). Please include the unit in the receipt. Please "
    "offer a grade A or B unit if you have one. I need it to last 100 hours."
)
PLAIN = f"I want to buy kimchi ({SKU})"
ELEVEN_IM = f"I want to buy kimchi ({SKU}), it must last at least 100 hours"


def picked(text: str) -> Unit | None:
    units = offerable_units(STOCK, text)
    return pick_unit(units, Request((SKU,), None, text), NOW, set())


def test_lower_grade_buyers_get_c_then_b() -> None:
    assert picked(DAE_YANGKIM) is C_OLD
    assert picked(KIMAUVE) is C_OLD


def test_psyoung82_gets_b_first_and_never_c() -> None:
    assert low_grade_order(PSYOUNG) == {"B": 0, "A": 1}
    assert picked(PSYOUNG) is B_MID


def test_plain_form_keeps_a_b_first_and_no_c_buyers_stay_out() -> None:
    assert picked(PLAIN) is not C_OLD
    # 11im's no-C form is not a lower-grade buyer and is never offered C.
    assert low_grade_order(ELEVEN_IM) is None
    assert picked(ELEVEN_IM) is not C_OLD


def test_stated_need_gets_a_unit_that_expires_before_close_first() -> None:
    # Given: NOW is about 102 h before the close; an A unit lasting past it and a C
    # unit that meets a 50 h need but expires before the close.
    lasting_a = food("la", "A", 600)
    closing_c = food("cc", "C", 80, cost=4000)
    text = (
        f"I want to buy kimchi ({SKU}). Quote price, grade and warranty. "
        "Must last at least 50 hours."
    )
    units = (lasting_a, closing_c)
    assert pick_unit(units, Request((SKU,), None, text), NOW, set()) is closing_c
    # A buyer that asks for A or B by name still gets A.
    named = f"I want to buy kimchi ({SKU}), grade A or B only, at least 50 hours"
    assert pick_unit(units, Request((SKU,), None, named), NOW, set()) is lasting_a
    # A need the closing unit cannot meet is not given it.
    long_need = f"I want to buy kimchi ({SKU}). Must last at least 90 hours."
    assert pick_unit(units, Request((SKU,), None, long_need), NOW, set()) is lasting_a


def test_c_takers_get_the_soonest_expiring_c() -> None:
    soon = food("cs", "C", 150, cost=4000)  # meets kimauve's 100 h need
    later = food("cl", "C", 300, cost=3900)
    units = (later, soon, A_FRESH)
    assert pick_unit(units, Request((SKU,), None, KIMAUVE), NOW, set()) is soon


def test_plain_form_no_longer_gets_48h_food_first() -> None:
    urgent_dear = food("ud", "A", 30, cost=6000)
    fresh_cheap = food("fc", "A", 500, cost=4000)
    units = (urgent_dear, fresh_cheap)
    assert pick_unit(units, Request((SKU,), None, PLAIN), NOW, set()) is fresh_cheap
    # Other openings still clear food close to expiry first.
    other = f"I want to buy {SKU}"
    assert pick_unit(units, Request((SKU,), None, other), NOW, set()) is urgent_dear


def test_fair_furniture_form_is_never_offered_c() -> None:
    text = f"I want to buy {SKU}. Please quote a price in credits."
    assert offerable_units(STOCK, text) == (A_FRESH, B_MID)
    # With only C on the shelf the answer is "none in stock", not a C unit.
    units = offerable_units((C_OLD,), text)
    assert pick_unit(units, Request((SKU,), None, text), NOW, set()) is None


def test_totato_gets_food_c_first() -> None:
    text = (
        f"I want to buy kimchi ({SKU}). Quote the total price. Require a market "
        "certificate. Confirm available stock, true grade, total price and warranty."
    )
    assert picked(text) is C_OLD


def test_yeonsong2_is_not_offered_dear_goods() -> None:
    from r3.market_files import Unit  # noqa: PLC0415 - test-local fixture

    def goods(name: str, sku: str, price: int) -> Unit:
        return Unit(
            unit=name,
            sku=sku,
            category="가구/침구>가구>소파/의자/스툴",
            kind="goods",
            list_price=price,
            cost=price // 2,
            grade="A",
            supplied_at=NOW - 3600,
        )

    sofa = goods("sofa", "G00000005", 2_000_000)
    pillow = goods("pillow", "G00000006", 40_000)
    text = "I want to buy sofa (G00000005), grade A or B only"
    assert offerable_units((sofa, pillow), text) == (pillow,)
    assert offerable_units((sofa, pillow), "I want to buy sofa (G00000005)") == (
        sofa,
        pillow,
    )


def test_leeuichann_form_is_never_offered_c() -> None:
    text = f"I want to buy one unit of SKU {SKU}. I need to use within 100 hours"
    assert C_OLD not in offerable_units(STOCK, text)
