"""v2.8 (2026-11-08, high-end furniture never stocked): with shop.goods_cost_frac set, the below-market restock check
prices goods at that share of list instead of the p75 of our units' cost/list. 10:40 live: p75 0.69-0.70, furniture
supplied in 48 h cost a mean of 0.60, and 4 sofas and 1 bed settling at 0.67-0.68 of list were never bought."""

import copy

import policy as P

from tests import test_price_market as M


def _pol(gcf=None):
    pol = copy.deepcopy(P.load())
    if gcf is not None:
        pol["shop"]["goods_cost_frac"] = gcf
    return pol


def test_without_the_key_the_p75_of_our_units_still_skips():
    buy, why = M._plan(
        _pol(), units_cost_frac=0.7
    )  # 100000 x 0.7 x 1.05 = 73500 > every p50
    assert buy == [] or not {b["sku"] for b in buy} & {"A1", "A2", "B1"}, (buy, why)
    assert any("under cost 0.70" in n for n in why["notes"])


def test_goods_cost_frac_replaces_the_p75_for_goods():
    buy, why = M._plan(
        _pol(0.55), units_cost_frac=0.7
    )  # 100000 x 0.55 x 1.05 = 57750 < 60000
    assert {b["sku"] for b in buy} == {"A1", "B1"}, (buy, why)


def test_goods_cost_frac_still_skips_skus_under_it():
    buy, why = M._plan(
        _pol(0.6), units_cost_frac=0.7
    )  # 63000: A1 60000 and B1 62000 skipped, A2 70000 kept
    assert {b["sku"] for b in buy} == {"A2"}, (buy, why)
    assert any("under cost 0.60" in n for n in why["notes"])


def test_food_keeps_the_units_p75():
    assert P.below_market_cost_frac(0.7, "food", 0.55) == 0.7
    assert P.below_market_cost_frac(0.7, "goods", 0.55) == 0.55
    assert P.below_market_cost_frac(0.7, "goods", None) == 0.7


def test_skip_below_market_off_ignores_the_key():
    pol = _pol(0.55)
    pol["shop"]["skip_below_market"] = False
    buy, _ = M._plan(pol, units_cost_frac=0.7)
    assert {b["sku"] for b in buy} == {"A1", "B1"}


def test_validate_bounds_goods_cost_frac():
    for bad in (0.3, 0.95, True, "0.6"):
        try:
            P.validate(_pol(bad))
        except P.PolicyError:
            continue
        raise AssertionError(bad)
    P.validate(_pol(0.62))
