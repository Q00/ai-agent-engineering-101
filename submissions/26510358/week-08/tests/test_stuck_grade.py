"""2026-11-11 (the human's yes): the adaptive cap's ceiling goes from 0.40 to 0.60 of the balance (it asked to grow 29
times in 36 h and was clipped each time), and, before the extra stock comes in, a goods SKU with a grade that will not
sell out by the round's end is not bought again."""
import time

import restock_r3
from r3lib import cashflow, config

NOW = 1_800_000_000.0


class Mem:
    def __init__(self, sales=None, sell=(0, 0), stockouts=0.0, cap=None):
        self.sales, self.sell, self.stockouts, self.kv = sales or {}, sell, stockouts, {}
        if cap:
            self.kv["cash_cap"] = {"cap": cap, "ts": 0}

    def my_sku_grade_sales(self, sku, hours=24):
        return self.sales.get(sku, {})

    def get(self, k, default=None):
        return self.kv.get(k, default)

    def put(self, k, v):
        self.kv[k] = v

    def sell_through(self, hours=12):
        sold, received = self.sell
        return {"c": {"sold": sold, "received": received}}

    def stockout_rates(self, now=None):
        return {"c": self.stockouts}


def units(sku, grade, n, age_h, kind="goods"):
    return [{"unit": f"{sku}{grade}{i}", "sku": sku, "grade": grade, "kind": kind, "cost": 100_000,
             "supplied_at": NOW - age_h * 3600} for i in range(n)]


def test_a_grade_our_pace_will_not_clear_by_the_end_stops_its_sku(monkeypatch):
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: 6 * 24)
    shelf = (units("Gsofa", "C", 13, 140) + units("Gchair", "A", 65, 50) + units("Gchair", "C", 12, 66)
             + units("Gnamu", "C", 25, 60) + units("Gnamu", "A", 25, 30))
    mem = Mem(sales={"Gsofa": {}, "Gchair": {"A": 2, "B": 12}, "Gnamu": {"A": 40, "B": 16, "C": 11}})
    # sofa C: 0 a day; chair A: 2 a day x 6 days = 12 < 65 (A comes first in sorted order); namu C: 11 x 6 = 66 >= 25
    assert restock_r3.stuck_skus(shelf, mem, now=NOW) == {"Gchair": "A", "Gsofa": "C"}


def test_young_units_few_units_and_food_are_not_stuck(monkeypatch):
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: 6 * 24)
    shelf = units("Gnew", "C", 20, 10) + units("Gfew", "C", 2, 90) + units("Gkimchi", "C", 40, 30, kind="food")
    assert restock_r3.stuck_skus(shelf, Mem(), now=NOW) == {}


def test_near_the_end_the_same_pace_clears_less(monkeypatch):
    shelf = units("Gx", "C", 10, 30)
    mem = Mem(sales={"Gx": {"C": 2}})
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: 6 * 24)
    assert restock_r3.stuck_skus(shelf, mem, now=NOW) == {}                  # 2 x 6 days = 12 >= 10
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: 3 * 24)
    assert restock_r3.stuck_skus(shelf, mem, now=NOW) == {"Gx": "C"}         # 2 x 3 days = 6 < 10


def test_the_cap_grows_to_the_new_ceiling_and_still_shrinks_on_slow_sales():
    balance = 200_000_000
    grow = Mem(sell=(90, 100), stockouts=5.0, cap=80_000_000)
    cap, _, _, why = cashflow.caps(grow, balance, stock_cost=80_000_000, now=NOW)
    assert why["ceiling"] == int(config.CAP_CEILING_SHARE * balance) == 120_000_000
    assert why["move"] == "grow" and cap == int(80_000_000 * config.CAP_GROW) > int(0.40 * balance)
    slow = Mem(sell=(30, 100), cap=110_000_000)
    cap, _, _, why = cashflow.caps(slow, balance, stock_cost=60_000_000, now=NOW)
    assert why["move"] == "shrink" and cap == int(110_000_000 * config.CAP_SHRINK)
