"""v2.13 (2026-11-11, stockouts): restock ranks the SKUs buyers asked our shop for and we lacked before the market's
top-settling SKUs. 13:34 labpc: 61 no_stock refusals in 1 h, 26 for one water SKU with 1 unit on hand, while 4 h of
restock bought three other drinks SKUs and none of it."""

import copy
import math

from tests import test_v123_spend_frac as B

P, NOW = B.P, B.NOW
D = "식품>음료>주스/코코아/생수"
CATALOG = {
    "Gaaaa0001": {"sku": "Gaaaa0001", "category": D, "kind": "food", "list_price": 9000, "shelf_hours": 720},
    "Gbbbb0002": {"sku": "Gbbbb0002", "category": D, "kind": "food", "list_price": 10000, "shelf_hours": 720},
}
DEMAND = [{"category": D, "kind": "food", "median_price": 9500}]


class Stats:
    def sku(self, k):
        return {"Gaaaa0001": {"settles_all": 100}}.get(k)

    def category(self, k):
        return {D: {"velocity_per_h": 50.0}}.get(k)


def _plan(asked, on=None):
    pol = copy.deepcopy(B._pol())
    pol["categories"][D] = {"stock_target": 10, "qty_per_buy": 6, "skus_per_category": 1}
    if on is not None:
        pol["shop"]["restock_asked_first"] = on
    inv = {"balance": 50_000_000, "units": [], "storage_per_day": 0,
           "restock": {"open": True, "opens_at": NOW - 10, "closes_at": NOW + 170}}
    buy, why = P.restock_plan(inv, DEMAND, CATALOG, pol, Stats(), activity={"own_asked": asked}, now=NOW)
    return [b["sku"] for b in buy], why


def test_without_asks_the_market_top_sku_is_bought():
    assert _plan({})[0] == ["Gaaaa0001"]


def test_asked_sku_comes_first_and_is_noted():
    skus, why = _plan({"Gbbbb0002": [5.0, NOW - 60]})
    assert skus == ["Gbbbb0002"] and any("asked first" in n for n in why["notes"])


def test_old_or_few_asks_do_not_count_and_switch_off():
    assert _plan({"Gbbbb0002": [5.0, NOW - 5 * 3600]})[0] == ["Gaaaa0001"]      # decayed below ASKED_MIN
    assert _plan({"Gbbbb0002": [1.0, NOW]})[0] == ["Gaaaa0001"]
    assert _plan({"Gbbbb0002": [5.0, NOW]}, on=False)[0] == ["Gaaaa0001"]
    assert _plan({"Gbbbb0002": "깨짐", "Gzzzz": [None, 1]})[0] == ["Gaaaa0001"]


def test_own_asked_decay():
    got = P.own_asked({"own_asked": {"G1": [8.0, NOW - 3600], "G2": [4.0, NOW - 3600]}}, NOW)
    assert set(got) == {"G1"} and abs(got["G1"] - 8.0 / math.e) < 1e-9
