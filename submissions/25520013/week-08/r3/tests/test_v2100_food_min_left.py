"""v2.10 (2026-11-09, short-shelf food reviews): with shop.food_min_left_frac set, a food request that states no
shelf-life need is not served from a unit with less than that share of its shelf left. 30 h to 11:00 KST: such sales
under 0.5 of the shelf drew 'Only N h of shelf life left' in 23-44% of reviews (3.65-4.09 stars), 0-9% above it."""

import copy

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
BASE = P.load(R3 / "policy.json")
NOW = 1_000_000.0
SHELF = 100.0


def _pol(frac=None):
    pol = copy.deepcopy(BASE)
    if frac is not None:
        pol["shop"]["food_min_left_frac"] = frac
    return pol


def _food(uid, hours_left):
    return {
        "unit": uid,
        "sku": "G0fa0d001",
        "name": "tomato",
        "category": "식품>신선식품>신선야채(부패주의)",
        "kind": "food",
        "grade": "B",
        "cost": 5_000,
        "list_price": 10_000,
        "supplied_at": 0,
        "expires_at": NOW + hours_left * 3600,
    }


def _find(units, pol, text="I want to buy tomato (G0fa0d001)"):
    why = {}
    u = S.find_unit(
        text, {"units": units}, pol=None, now=NOW, shelf=lambda s: SHELF, why=why
    )
    u2 = S.find_unit(
        text, {"units": units}, pol=pol, now=NOW, shelf=lambda s: SHELF, why=why
    )
    return u, u2, why


def test_without_the_key_the_freshest_short_unit_still_sells():
    u, u2, _ = _find([_food("a", 30), _food("b", 40)], _pol())
    assert u["unit"] == "b" and u2["unit"] == "b"


def test_with_the_key_no_unit_under_the_share_is_sold():
    _, u2, why = _find([_food("a", 30), _food("b", 40)], _pol(0.5))
    assert u2 is None and why.get("short_shelf") == 0.5 and "need_h" not in why


def test_with_the_key_a_unit_over_the_share_is_sold():
    _, u2, _ = _find([_food("a", 30), _food("b", 60)], _pol(0.5))
    assert u2["unit"] == "b"


def test_units_over_the_unknown_need_share_are_unaffected():
    _, u2, _ = _find([_food("a", 80), _food("b", 90)], _pol(0.5))
    assert u2["unit"] == "a"


def test_a_stated_need_is_unaffected():
    _, u2, why = _find(
        [_food("a", 30)],
        _pol(0.5),
        text="I want to buy tomato (G0fa0d001). I need 20 h.",
    )
    assert u2["unit"] == "a"


def test_validate_bounds():
    for bad in (-0.1, 0.95, True, "0.5"):
        try:
            P.validate(_pol(bad))
        except P.PolicyError:
            continue
        raise AssertionError(bad)
    P.validate(_pol(0.5))
    P.validate(_pol(0))
