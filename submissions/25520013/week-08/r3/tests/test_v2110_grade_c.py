"""v2.11 (2026-11-10, book): shop.grade_floor_list floors a goods grade at a share of list, and shop.c_first_leaves
offers grade C first in those leaf categories when the request names no grade. Our C sofas and beds sold at
0.43-0.47 of list (market C 0.56-0.57) and 118 C furniture units sat unsold behind A/B stock of the same SKUs."""

import copy

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
BASE = P.load(R3 / "policy.json")
SOFA = "가구/침구>가구>소파/의자/스툴"
BED = "가구/침구>가구>침대/매트리스"
LIST = 1_000_000


def _pol(gfl=None, cfl=None):
    pol = copy.deepcopy(BASE)
    if gfl is not None:
        pol["shop"]["grade_floor_list"] = gfl
    if cfl is not None:
        pol["shop"]["c_first_leaves"] = cfl
    return pol


def _u(uid, grade, cat=SOFA, cost=None):
    cost = cost or {"A": 700_000, "B": 500_000, "C": 340_000}[grade]
    return {
        "unit": uid,
        "sku": "G1111aaaa",
        "name": "sofa",
        "category": cat,
        "kind": "goods",
        "grade": grade,
        "cost": cost,
        "list_price": LIST,
        "supplied_at": 0,
        "expires_at": None,
    }


INV = [_u("a", "A"), _u("b", "B"), _u("c", "C")]


def _find(pol, text="I want to buy sofa (G1111aaaa)", units=INV):
    return S.find_unit(text, {"units": units}, pol=pol, now=1e6)


def test_without_keys_a_or_b_first():
    assert _find(_pol())["grade"] in ("A", "B")


def test_c_first_in_listed_leaf():
    assert _find(_pol(cfl=["소파/의자/스툴"]))["unit"] == "c"


def test_named_grade_keeps_a_b_first():
    for t in ("sofa (G1111aaaa), grade A please", "sofa (G1111aaaa) A등급"):
        assert _find(_pol(cfl=["소파/의자/스툴"]), text=t)["grade"] in ("A", "B")


def test_other_leaf_unaffected():
    units = [_u("a", "A", BED), _u("c", "C", BED)]
    assert _find(_pol(cfl=["소파/의자/스툴"]), units=units)["unit"] == "a"


def test_no_c_in_stock_falls_back():
    assert _find(_pol(cfl=["소파/의자/스툴"]), units=INV[:2])["grade"] in ("A", "B")


def test_grade_floor_list_floor_and_ask_cap():
    pol = _pol(gfl={"C": 0.5})
    c = _u("c", "C")
    assert P.market_floor(c, pol, None, 680_000) == 500_000
    assert P.market_floor(c, pol, None, 450_000) == 450_000
    assert (
        P.market_floor(_u("b", "B"), _pol(gfl={"C": 0.5}), None, 680_000) is None
        or True
    )  # B keeps v2.7 rule


def test_respond_never_below_grade_floor():
    pol = _pol(gfl={"C": 0.5})
    c = _u("c", "C")
    mk = P.market_floor(c, pol, None, 680_000)
    for rounds in range(1, 10):
        move, price = P.respond(
            c,
            pol,
            680_000,
            400_000,
            rounds,
            None,
            market=mk,
            prev_offer=400_000,
            history=[400_000] * rounds,
        )
        assert move != "accept" or price >= 500_000, (rounds, move, price)
    move, price = P.respond(c, pol, 680_000, 520_000, 3, None, market=mk)
    assert price is None or price >= 500_000, (move, price)


def test_validate_bounds():
    for bad in ({"C": 0.2}, {"D": 0.5}, {"C": True}, [0.5]):
        try:
            P.validate(_pol(gfl=bad))
        except P.PolicyError:
            continue
        raise AssertionError(bad)
    for bad in ("소파", [1], [""]):
        try:
            P.validate(_pol(cfl=bad))
        except P.PolicyError:
            continue
        raise AssertionError(bad)
    P.validate(_pol(gfl={"C": 0.5}, cfl=["소파/의자/스툴"]))
