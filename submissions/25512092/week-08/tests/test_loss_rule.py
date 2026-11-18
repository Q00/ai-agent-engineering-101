"""Below cost only by the stoploss (person 11/12 ~14:40, loss_rule.json), through the seller's floor chain (seller_r3:
pricing.floor then high_ask.floor): aging stops at cost; the stoploss share is days left x 1% (cost at most that above
the average -> the average; high price, over-cost and sofas too); more above: never under cost; the overstock 0.95
variant never under cost; the endgame unchanged; all as before while loss_rule.json is off.
Live files untouched (temp rules, market median and overcost rules faked).
    python tests/test_loss_rule.py"""
import contextlib
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import pricing  # noqa: E402
from depts import high_ask, overcost, sales, sofa  # noqa: E402

NOW = time.time()
HA = {"enabled": True, "cap": True, "floor_rule": {"enabled": True, "cost": 1.05, "median": 0.8},
      "c_step": {"enabled": True, "h1": 12, "h2": 24, "cost": 1.1, "cost2": 1.0, "median": 0.6}}
ON = {"enabled": True, "per_day": 0.01}


def seller_floor(u, avg, on, case="normal", is_sofa=False, mult=1.0):
    with contextlib.ExitStack() as es:
        es.enter_context(mock.patch.object(sales, "LOSS_RULE", tmp_json(ON if on else {}, "loss_rule")))
        es.enter_context(mock.patch.object(sales, "ROUND_END", NOW + 5 * 86400))     # 5 days left -> 5%
        es.enter_context(mock.patch.object(high_ask, "RULES", tmp_json(HA, "high_ask")))
        es.enter_context(mock.patch.object(overcost, "RULES", tmp_json({"enabled": True}, "overcost")))
        es.enter_context(mock.patch.object(sofa, "market", lambda sku, now=None: (avg, 50, None, None)))
        es.enter_context(mock.patch.object(sofa, "applies", lambda u: is_sofa))
        es.enter_context(mock.patch.object(sofa, "floor", lambda u, now=None: (int(u["cost"] * 1.05), "sofa")))
        es.enter_context(mock.patch("depts.volume.floor", lambda u, now=None: None))
        reserve, rule = pricing.floor(u, avg, case, NOW, overstock_mult=mult)
        return high_ask.floor(u, avg, reserve, case, NOW, rule), rule


def unit(cost, age_h=2, grade="A"):
    return {"unit": "u1", "sku": "S1", "cost": cost, "grade": grade, "kind": "goods", "category": "가구>침대",
            "supplied_at": NOW - age_h * 3600, "expires_at": None, "list_price": cost * 2}


def test_stoploss_share():
    with mock.patch.object(sales, "LOSS_RULE", tmp_json(ON, "loss_rule")), \
            mock.patch.object(sales, "ROUND_END", NOW + 5.5 * 86400):
        assert abs(sales.stoploss_share(NOW) - 0.055) < 1e-9
    with mock.patch.object(sales, "LOSS_RULE", tmp_json({}, "loss_rule")):
        assert sales.stoploss_share(NOW) == 0.02


def test_floors():
    # high, over cost by 4% (within 5%): the average; off: never under cost (4% > the old 2%)
    assert seller_floor(unit(1_040_000), 1_000_000, True)[0] == 1_000_000
    assert seller_floor(unit(1_040_000), 1_000_000, False)[0] >= 1_040_000
    assert seller_floor(unit(1_060_000), 1_000_000, True)[0] >= 1_060_000       # 6% above: never under cost
    # sofas: floor above the average and cost above it -> the same rule
    f, rule = seller_floor(unit(1_040_000), 1_000_000, True, is_sofa=True)
    assert f == 1_000_000 and rule == "stoploss", (f, rule)
    assert seller_floor(unit(1_060_000), 1_000_000, True, is_sofa=True) == (1_113_000, "sofa")
    assert seller_floor(unit(1_040_000), 1_000_000, False, is_sofa=True) == (1_092_000, "sofa")   # off: as before
    assert seller_floor(unit(900_000), 1_000_000, True, is_sofa=True) == (945_000, "sofa")       # cost under avg
    # aging stops at cost (normal goods, 60 h: step 6 -> avg x 0.4; off: 90% of cost)
    assert seller_floor(unit(90_000, age_h=60), 100_000, False)[0] == 81_000
    assert seller_floor(unit(90_000, age_h=60), 100_000, True)[0] == 90_000
    # normal goods: the stoploss share too
    assert seller_floor(unit(51_500), 50_000, True)[0] == 50_000
    # overstock 0.95 variant: never under cost while on
    assert seller_floor(unit(90_000), 100_000, False, case="overstock", mult=0.95)[0] == 85_500
    assert seller_floor(unit(90_000), 100_000, True, case="overstock", mult=0.95)[0] == 90_000
    # endgame first: its own floor, no stoploss
    assert seller_floor(unit(1_040_000), 1_000_000, True, case="endgame")[1] != "stoploss"


if __name__ == "__main__":
    test_stoploss_share()
    test_floors()
    print("ok")
