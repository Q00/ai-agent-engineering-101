"""A / B grade high-price floor after 24 h (person 11/12 ~14:25), through the seller's floor chain (seller_r3: pricing.floor
then high_ask.floor): max(cost x 1.00, 48 h market median x 0.70), never under cost; A / B only, not sofas, only
over 24 h; a lower existing floor (C grade steps, overcost, stoploss, aging) stays; off without age_ab.enabled.
Live files untouched (temp high_ask.json, market median faked).
    python tests/test_age_ab_floor.py"""
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import pricing  # noqa: E402
from depts import high_ask, overcost, sofa  # noqa: E402

NOW = time.time()
BASE = {"enabled": True, "cap": True, "floor_rule": {"enabled": True, "cost": 1.05, "median": 0.8},
        "c_step": {"enabled": True, "h1": 12, "h2": 24, "cost": 1.1, "cost2": 1.0, "median": 0.6}}
AB = {"enabled": True, "h": 24, "grades": ["A", "B"], "cost": 1.0, "median": 0.7}


def seller_floor(u, avg, rules, med=1_000_000, is_sofa=False):
    with mock.patch.object(high_ask, "RULES", tmp_json(rules, "high_ask")), \
            mock.patch.object(sofa, "market", lambda sku, now=None: (med, 50, None, None)), \
            mock.patch.object(sofa, "applies", lambda u: is_sofa), \
            mock.patch.object(overcost, "is_overcost", lambda u, avg: False), \
            mock.patch("depts.volume.floor", lambda u, now=None: None), \
            mock.patch("depts.sales.aging", lambda u, avg, now=None: (None, 0)):
        reserve, rule = pricing.floor(u, avg, "normal", NOW)
        return high_ask.floor(u, avg, reserve, "normal", NOW, rule)


def unit(grade="A", age_h=30, cost=600_000):
    return {"unit": "u1", "sku": "S1", "cost": cost, "grade": grade, "kind": "goods", "category": "가전>TV",
            "supplied_at": NOW - age_h * 3600, "expires_at": None, "list_price": 1_000_000}


def test_age_ab():
    on = dict(BASE, age_ab=AB)
    assert seller_floor(unit(), 900_000, BASE) == 800_000                     # off: max(630k, median 1M x 0.8)
    assert seller_floor(unit(), 900_000, on) == 700_000                       # on: max(600k, 1M x 0.7)
    assert seller_floor(unit("B"), 900_000, on) == 700_000
    assert seller_floor(unit(age_h=23), 900_000, on) == 800_000               # under 24 h: unchanged
    assert seller_floor(unit(), 900_000, on, is_sofa=True) != 700_000         # sofas: their own rules
    assert seller_floor(unit(), 900_000, on, med=500_000) == 600_000          # never under cost (median x 0.7 = 350k)
    assert seller_floor(unit("C"), 900_000, on) == 600_000                    # C over 24 h: its step (cost x 1.00) as before
    with mock.patch("depts.sales.aging", lambda u, avg, now=None: (550_000, 3)):   # an existing lower markdown stays
        with mock.patch.object(high_ask, "RULES", tmp_json(on, "high_ask")), \
                mock.patch.object(sofa, "market", lambda sku, now=None: (1_000_000, 50, None, None)), \
                mock.patch.object(sofa, "applies", lambda u: False), \
                mock.patch.object(overcost, "is_overcost", lambda u, avg: False), \
                mock.patch("depts.volume.floor", lambda u, now=None: None):
            r, rule = pricing.floor(unit(), 900_000, "normal", NOW)
            assert rule.startswith("aging") and high_ask.floor(unit(), 900_000, r, "normal", NOW, rule) == 550_000


if __name__ == "__main__":
    test_age_ab()
    print("ok")
