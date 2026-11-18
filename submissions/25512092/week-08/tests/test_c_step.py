"""high_ask.c_step_floor / floor (C grade high-price steps) and open_expiry.expire_s (11/09 ~15:05)."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts import high_ask, open_expiry, overcost, sofa  # noqa: E402
from depts import sales as _sales  # noqa: E402
_sales.LOSS_RULE = os.path.join(tempfile.mkdtemp(), "loss_rule.json")   # 11/12: the live switch is on; tests start off


def tmp(obj):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


NOW = time.time()
CS = {"enabled": True, "h1": 12, "h2": 24, "cost": 1.10, "cost2": 1.00, "median": 0.6}


def setup(cs=CS):
    high_ask.RULES = tmp({"enabled": True, "floor_rule": {"enabled": True, "cost": 1.05, "median": 0.8}, "c_step": cs})
    overcost.RULES = tmp({"enabled": True, "units": []})
    sofa.applies = lambda u: False
    sofa.market = lambda sku, now=None: (400000, 40, None, 4)        # median 400k


def u(grade="C", age=13, cost=200000):
    return {"unit": "x", "sku": "S", "grade": grade, "cost": cost, "supplied_at": NOW - age * 3600}


def test_steps():
    setup()
    assert high_ask.c_step_floor(u(age=6), NOW, CS) is None
    assert high_ask.c_step_floor(u(age=13), NOW, CS) == 240000                 # max(220k, 400k x 0.6)
    assert high_ask.c_step_floor(u(age=25), NOW, CS) == 200000                 # cost x 1.00 (11/09 ~15:15)
    assert high_ask.c_step_floor(u(grade="B", age=25), NOW, CS) is None


def test_floor():
    setup()
    assert high_ask.floor(u(age=6), 450000, 999999, "normal", now=NOW) == 320000     # max(210k, 320k): unchanged
    assert high_ask.floor(u(age=13), 450000, 999999, "normal", now=NOW) == 240000
    assert high_ask.floor(u(age=25), 450000, 999999, "normal", now=NOW) == 200000
    assert high_ask.floor(u(age=25), 450000, 150000, "normal", now=NOW, rule="aging step 3") == 150000  # lower markdown
    assert high_ask.floor(u(age=25, cost=500000), 450000, 999999, "normal", now=NOW) == 500000          # over-cost rule


def test_overcost_stoploss():
    setup()
    # cost 500k, avg 495k (1% above): the stoploss floor (the average) is allowed
    assert high_ask.floor(u(grade="A", cost=500000), 495000, 495000, "normal", now=NOW, rule="stoploss") == 495000
    # an aging floor lower still: not under the average
    assert high_ask.floor(u(grade="A", cost=500000), 495000, 400000, "normal", now=NOW, rule="aging step 4") == 495000
    # 4% above: never under cost
    assert high_ask.floor(u(grade="A", cost=500000), 480000, 480000, "normal", now=NOW, rule="stoploss") == 500000


def test_nostock_cause():
    from depts import nostock_cause as N
    us = [{"unit": "a", "sku": "S", "grade": "A"}, {"unit": "b", "sku": "S", "grade": "B"}]
    op = {"t1": {"unit": {"unit": "a"}, "t": NOW - 200}, "t2": {"unit": {"unit": "b"}, "t": NOW - 20}}
    c = N.classify("S", us, op, {}, NOW, 300)
    assert c["cause"] == "lock" and c["locked"] == 2 and c["near_expiry"] == 1 and c["lock_age_max"] == 200
    assert N.classify("S", us, {"t1": op["t1"]}, {"b": NOW}, NOW, 300)["cause"] == "lock"
    assert N.classify("S", us, {}, {"a": NOW, "b": NOW}, NOW, 300)["cause"] == "receipt"
    assert N.classify("S", us, {}, {"a": NOW}, NOW, 300)["cause"] == "other"
    assert N.classify("Z", us, {}, {}, NOW, 300)["cause"] == "other"
    setup(dict(CS, enabled=False))
    assert high_ask.floor(u(age=25), 450000, 999999, "normal", now=NOW) == 320000


def test_expire_s():
    old = open_expiry.RULES
    try:
        open_expiry.RULES = tmp({"expire_s": 300})
        assert open_expiry.expire_s() == 300
        o = {"a": {"t": NOW - 400}, "b": {"t": NOW - 200}}
        assert open_expiry.stale(o, NOW) == ["a"]
        open_expiry.RULES = os.path.join(HERE, "no_such.json")
        assert open_expiry.expire_s() == 600 and open_expiry.stale(o, NOW) == []
    finally:
        open_expiry.RULES = old


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
