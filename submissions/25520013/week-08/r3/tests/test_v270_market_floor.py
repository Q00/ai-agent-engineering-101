"""v2.7 (2026-11-07, endgame giveaways): with shop.market_floor_frac set, an A/B goods unit is never sold below that
share of its SKU's market settle price (shop.ask_quantile), in the endgame too. 24 h to 20:00 KST: 103 of 326 goods
sales went below 0.9 x market; one curtain SKU asked 0.75 of list (= market) and sold at 0.55-0.66 after 4-5 rounds."""

import copy
import inspect

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
BASE = P.load(R3 / "policy.json")
LIST = 100_000
CAT = "가구/침구>침구/홈데코>커튼/러그/쿠션/덮개"


class Stats:
    def __init__(self, rows):
        self.rows = rows

    def sku(self, key):
        return self.rows.get(key)

    def category(self, key):
        return None


MARKET = Stats({"G40e27aec": {"n": 99, "p50": 75_000, "p75": 78_000}})


def _pol(frac=0.9):
    pol = copy.deepcopy(BASE)
    if frac is not None:
        pol["shop"]["market_floor_frac"] = frac
    return pol


def _unit(grade="B", cost=44_000, kind="goods"):
    return {
        "unit": "u-1",
        "sku": "G40e27aec",
        "category": CAT,
        "kind": kind,
        "grade": grade,
        "cost": cost,
        "list_price": LIST,
        "supplied_at": 0,
        "expires_at": None,
    }


def test_market_floor_is_frac_of_the_sku_market_price_capped_at_the_ask():
    pol = _pol()
    assert P.market_floor(_unit(), pol, MARKET) == 67_500
    assert P.market_floor(_unit(), pol, MARKET, ask=60_000) == 60_000


def test_market_floor_is_off_without_the_key_for_food_grade_c_and_thin_stats():
    assert P.market_floor(_unit(), _pol(None), MARKET) is None
    assert P.market_floor(_unit(kind="food"), _pol(), MARKET) is None
    assert P.market_floor(_unit(grade="C"), _pol(), MARKET) is None
    assert (
        P.market_floor(_unit(), _pol(), Stats({"G40e27aec": {"n": 4, "p50": 75_000}}))
        is None
    )
    assert P.market_floor(_unit(), _pol(), None) is None


def _haggle(pol, market, offers=(50_000, 52_000, 54_000, 55_000, 55_000, 55_000)):
    """A buyer that creeps up then stalls at 55,000 (0.55 of list), as in the curtain sales."""
    u, ask, hist = _unit(), 75_000, []
    for r, off in enumerate(offers, start=1):
        move, price = P.respond(
            u,
            pol,
            ask,
            off,
            r,
            {"balance": 10**9},
            prev_offer=hist[-1] if hist else None,
            history=list(hist),
            market=market,
        )
        hist.append(off)
        if move != "counter":
            return move, price
    return "open", None


def test_without_market_the_endgame_takes_the_cost_floor_as_before():
    move, price = _haggle(_pol(), None)
    assert (move, price) == ("accept", 55_000)


def test_with_market_the_stalled_buyer_below_the_market_floor_is_not_accepted():
    move, price = _haggle(_pol(), 67_500)
    assert move != "accept"


def test_with_market_a_buyer_reaching_the_market_floor_is_accepted():
    move, price = _haggle(_pol(), 67_500, offers=(60_000, 64_000, 67_500, 67_500, 67_500))
    assert (move, price) == ("accept", 67_500)


def test_market_floor_is_ignored_when_liquidating_or_relaxing():
    pol = _pol()
    u = _unit()
    low = {"balance": pol["shop"]["liquidation_line"] - 1}
    assert (
        P.respond(
            u,
            pol,
            75_000,
            50_000,
            6,
            low,
            prev_offer=50_000,
            history=[50_000],
            market=67_500,
        )[0]
        == "accept"
    )
    assert P.floor_price(u, pol, {"balance": 10**9}, relax=1.0) < 67_500
    move, _ = P.respond(
        u,
        pol,
        75_000,
        56_000,
        6,
        {"balance": 10**9},
        relax=1.0,
        prev_offer=56_000,
        history=[56_000],
        market=67_500,
    )
    assert move == "accept"


def test_validate_bounds_market_floor_frac():
    for bad in (0.4, 1.1, True, "0.9"):
        pol = _pol(bad)
        try:
            P.validate(pol)
        except P.PolicyError:
            continue
        raise AssertionError(bad)
    P.validate(_pol(0.9))


def test_the_seller_passes_the_market_floor_to_respond():
    src = inspect.getsource(S.Seller.decide)
    assert 'P.market_floor(u, pol, deal.get("stats"), deal["ask"])' in src
    assert "market=mk)" in src
