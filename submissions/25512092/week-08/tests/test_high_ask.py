"""High-price first ask <= market average, clearance at 98% (never under cost), buy filter (11/08 ~17:00).
    python tests/test_high_ask.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import high_ask as ha  # noqa: E402

R = {"enabled": True, "cap": True, "buy_filter": True, "clear": {"GCLR": 0.98}}
ha.rules = lambda: R
from depts import overcost  # noqa: E402
overcost.rules = lambda: {}          # the live overcost_high.json (on since 11/09 15:07) is tested in test_overcost.py


def u(sku, cost):
    return {"sku": sku, "cost": cost, "list_price": cost * 2}


def test_cap():
    assert ha.first_ask(u("GX", 500_000), 600_000, 560_000, 640_000) == (600_000, "cap at avg 600,000")
    assert ha.first_ask(u("GX", 500_000), 600_000, 560_000, 590_000) == (590_000, None)     # under avg: kept
    assert ha.first_ask(u("GX", 500_000), 600_000, 620_000, 650_000)[0] == 620_000          # floor kept
    assert ha.first_ask(u("GX", 50_000), 60_000, 55_000, 70_000) == (70_000, None)          # low price: untouched
    assert ha.first_ask(u("GX", 500_000), None, 560_000, 640_000) == (640_000, None)        # no average


def test_clear():
    # G3e0d6c67-like: cost 1,223,636, avg 1,400,000 -> 1,372,000; floor 1,323,000 stays (under the ask)
    x = u("GCLR", 1_223_636)
    assert ha.floor(x, 1_400_000, 1_323_000) == 1_323_000
    assert ha.first_ask(x, 1_400_000, 1_323_000, 1_438_000)[0] == 1_372_000
    # floor above 98%: lowered to the ask
    assert ha.floor(x, 1_350_000, 1_340_000) == 1_323_000
    # 98% of the average under cost: never under cost
    assert ha.floor(x, 1_200_000, 1_300_000) == 1_223_636
    assert ha.first_ask(x, 1_200_000, 1_223_636, 1_300_000)[0] == 1_223_636
    assert ha.floor(u("GX", 500_000), 600_000, 590_000) == 590_000                          # not listed


def test_off():
    global R
    keep = dict(R)
    R.clear(); R.update(enabled=False)
    assert ha.first_ask(u("GX", 500_000), 600_000, 560_000, 640_000) == (640_000, None)
    assert ha.buy_ok("GX", {}, 500_000, 600_000, "normal") == (True, None, None)
    R.clear(); R.update(keep)


def test_buy_ok():
    calls = {}

    def fake(sku, c, cost, avg, case, now=None):
        calls[sku] = cost
        return (int(cost * 1.05) if cost * 1.05 > avg else int(avg)), int(cost * 1.05)
    real = ha.expected_first_ask
    ha.expected_first_ask = fake
    try:
        assert ha.buy_ok("GA", {}, 500_000, 600_000, "normal")[0] is True       # floor 525k < avg: ask = avg
        ok, ask, fl = ha.buy_ok("GB", {}, 600_000, 610_000, "normal")           # floor 630k > avg: skip
        assert ok is False and ask == 630_000 and fl == 630_000
    finally:
        ha.expected_first_ask = real


def test_floor_rule():
    from depts import sofa
    real, real_app = sofa.market, sofa.applies
    med = {"GM": 1_000_000, "GLOW": 300_000, "GCLR": 1_438_000, "GSOFA": 900_000}
    sofa.market = lambda sku, now=None: (med.get(sku), 10, 0, 0)
    sofa.applies = lambda x: x.get("sku") == "GSOFA"
    keep = dict(R)
    try:
        R["floor_rule"] = {"enabled": False, "cost": 1.05, "median": 0.80}
        assert ha.floor(u("GM", 600_000), 900_000, 920_000, "normal", rule="volume") == 920_000    # off: unchanged
        R["floor_rule"]["enabled"] = True
        assert ha.floor(u("GM", 600_000), 900_000, 920_000, "normal", rule="volume") == 800_000    # median x 0.8
        assert ha.floor(u("GM", 800_000), 900_000, 920_000, "normal", rule="volume") == 840_000    # cost x 1.05
        assert ha.floor(u("GNONE", 200_000), 250_000, 230_000, "normal", rule="normal") == 210_000  # no median
        # a markdown floor lower than the new one stays (the Samsung TV: aging 420,011 < cost x 1.05)
        assert ha.floor(u("GLOW", 449_244), 420_011, 420_011, "normal", rule="aging step 0") == 420_011
        assert ha.floor(u("GLOW", 449_244), 420_011, 420_000, "normal", rule="stoploss") == 420_000
        assert ha.floor(u("GLOW", 449_244), 420_011, 400_000, "normal", rule="clearance") == 400_000
        assert ha.floor(u("GM", 600_000), 900_000, 600_000, "cash_low", rule="cash_low") == 600_000      # cash low
        assert ha.floor(u("GM", 600_000), 900_000, 540_000, "overstock", rule="overstock") == 540_000    # overstock
        assert ha.floor(u("GM", 600_000), 900_000, 900_000, "normal", rule="normal") == 800_000          # not a markdown
        # a markdown floor higher than the new one: the new (lower) one
        assert ha.floor(u("GM", 600_000), 900_000, 950_000, "normal", rule="aging step 0") == 800_000
        assert ha.floor(u("GSOFA", 600_000), 900_000, 830_000, "normal", rule="sofa") == 830_000   # sofa: unchanged
        assert ha.floor(u("GM", 600_000), 900_000, 500_000, "endgame", rule="endgame step 1") == 500_000
        assert ha.floor(u("GM", 50_000), 90_000, 70_000, "normal", rule="volume") == 70_000        # not high price
        # cleared SKU: new floor max(1,284,817, 1,150,400) = 1,284,817 < 98% ask
        assert ha.floor(u("GCLR", 1_223_636), 1_410_607, 1_321_611, "normal", rule="volume") == 1_284_817
        assert ha.first_ask(u("GM", 600_000), 900_000, 800_000, 1_000_000)[0] == 900_000            # cap at avg
        assert ha.first_ask(u("GM", 880_000), 900_000, 924_000, 1_000_000)[0] == 924_000            # floor wins
    finally:
        sofa.market, sofa.applies = real, real_app
        R.clear(); R.update(keep)


if __name__ == "__main__":
    test_cap(); test_clear(); test_off(); test_buy_ok(); test_floor_rule()
    print("ok")
