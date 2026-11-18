"""depts/overcost.py and its hooks: the floor cost x 1.00 (never under cost), the decision once per unit."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts import high_ask, overcost as O, sofa  # noqa: E402
from depts import sales as _sales  # noqa: E402
_sales.LOSS_RULE = os.path.join(tempfile.mkdtemp(), "loss_rule.json")   # 11/12: the live switch is on; tests start off


def tmp(obj):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


def setup(units=()):
    O.RULES = tmp({"enabled": True, "units": list(units)})
    O.SEEN = tempfile.mktemp(suffix=".json")
    high_ask.RULES = tmp({"enabled": True, "cap": True, "floor_rule": {"enabled": True, "cost": 1.05, "median": 0.8}})
    sofa.applies = lambda u: False
    sofa.market = lambda sku, now=None: (250000, 40, None, 4)       # median 250k -> x 0.8 = 200k


def test_is_overcost():
    setup(["listed"])
    assert O.is_overcost({"unit": "u", "sku": "S", "cost": 300000}, 290000)
    assert not O.is_overcost({"unit": "u", "sku": "S", "cost": 280000}, 290000)
    assert O.is_overcost({"unit": "listed", "sku": "S", "cost": 280000}, 290000)      # floor 294k > avg: listed
    assert not O.is_overcost({"unit": "u", "sku": "S", "cost": 90000}, 50000)          # not high price
    O.RULES = tmp({"enabled": False})
    assert not O.is_overcost({"unit": "u", "sku": "S", "cost": 300000}, 290000)


def test_floor():
    setup(["listed"])
    u = {"unit": "u", "sku": "S", "cost": 300000}
    assert high_ask.floor(u, 290000, 999999, "normal") == 300000                       # cost x 1.00 (was 315,000)
    assert high_ask.floor(dict(u, cost=280000), 400000, 999999, "normal") == 294000    # not over cost: x 1.05
    assert high_ask.floor(dict(u, unit="listed", cost=280000), 290000, 999999, "normal") == 280000
    assert high_ask.floor(u, 290000, 250000, "normal", rule="aging step 2") == 300000  # a lower markdown: not under cost


def test_mark_once():
    setup()
    seen = []
    import decisions
    decisions.record = lambda *a, **k: seen.append(a)
    units = [{"unit": "a", "sku": "S", "cost": 300000}, {"unit": "b", "sku": "T", "cost": 200000}]
    avg = {"S": 290000, "T": 250000}.get
    assert O.mark(units, avg, log=lambda m: None) == {"a"} and len(seen) == 1 and seen[0][1] == "overcost_high"
    assert O.mark(units, avg, log=lambda m: None) == {"a"} and len(seen) == 1


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
