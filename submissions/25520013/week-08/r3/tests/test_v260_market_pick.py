"""v2.6 (2026-11-07, thin furniture margin): among the A/B goods units of a SKU the seller offers first a unit whose floor
the SKU's market settle price (shop.ask_quantile) reaches. 19:30 KST: 33 of our 57 bed A units cost 0.75-0.85 of
list, floor above the bed market median 0.72, and the oldest-first pick offered them 109 times in an hour."""

import inspect

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
POL = P.load(R3 / "policy.json")
BED = "가구/침구>가구>침대/매트리스"
NOW = 1_794_000_000.0
LIST = 1_000_000


class Stats:
    def __init__(self, rows):
        self.rows = rows

    def sku(self, key):
        return self.rows.get(key)

    def category(self, key):
        return None


def _unit(uid, cost_frac, supplied, grade="A", sku="G8ec51935"):
    return {
        "unit": uid,
        "sku": sku,
        "category": BED,
        "kind": "goods",
        "grade": grade,
        "name": "침대",
        "cost": int(LIST * cost_frac),
        "list_price": LIST,
        "supplied_at": supplied,
        "expires_at": None,
    }


def _pick(units, stats=None, text="G8ec51935 침대 하나 주세요"):
    return S.find_unit(text, {"units": units}, pol=POL, now=NOW, stats=stats)


UNITS = [_unit("u-old", 0.80, NOW - 9000), _unit("u-new", 0.60, NOW - 100)]
MARKET = Stats(
    {"G8ec51935": {"n": 40, "p50": int(LIST * 0.72), "p75": int(LIST * 0.76)}}
)


def test_a_unit_the_market_price_reaches_goes_first():
    assert _pick(UNITS, MARKET)["unit"] == "u-new"


def test_without_stats_the_pick_is_oldest_first_as_before():
    assert _pick(UNITS)["unit"] == "u-old"


def test_too_few_settles_keeps_oldest_first():
    few = Stats({"G8ec51935": {"n": 4, "p50": int(LIST * 0.72)}})
    assert _pick(UNITS, few)["unit"] == "u-old"


def test_when_no_unit_is_reachable_the_oldest_still_goes():
    low = Stats({"G8ec51935": {"n": 40, "p50": int(LIST * 0.50)}})
    assert _pick(UNITS, low)["unit"] == "u-old"


def test_among_reachable_units_oldest_first_is_kept():
    units = UNITS + [_unit("u-mid", 0.62, NOW - 5000)]
    assert _pick(units, MARKET)["unit"] == "u-mid"


def test_grade_a_b_still_beats_grade_c():
    units = [_unit("u-c", 0.30, NOW - 9999, grade="C"), _unit("u-a", 0.80, NOW - 100)]
    assert _pick(units, MARKET)["unit"] == "u-a"


def test_the_floor_compare_uses_the_policy_floor():
    u = UNITS[0]
    assert P.floor_price(u, POL, None, NOW) > int(LIST * 0.72)
    assert S.sells_at_market(u, POL, MARKET, None, NOW) is False
    assert S.sells_at_market(UNITS[1], POL, MARKET, None, NOW) is True


def test_the_seller_passes_stats_to_the_pick():
    src = inspect.getsource(S.Seller._execute)
    assert "find_unit(text, inv, self.held(now), self.pol" in src
    assert "stats=st)" in src
    assert (
        src.index("st = self.stats()")
        < src.index("find_unit(text")
        < src.index("P.ask_price(unit, pol, stats=st")  # v2.12 arms: the ask uses the arm-applied policy
    )
