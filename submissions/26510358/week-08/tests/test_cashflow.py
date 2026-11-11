"""Adaptive stock cap (r3lib/cashflow.py), no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import cashflow, config


class Mem:
    def __init__(self, sold=0, received=0, turned=0.0):
        self.kv, self.st, self.turned = {}, {"k": {"received": received, "sold": sold}}, turned

    def get(self, k):
        return self.kv.get(k)

    def put(self, k, v):
        self.kv[k] = v

    def sell_through(self, hours=24):
        return self.st

    def stockout_rates(self, now=None):
        return {"k": self.turned}


def test_grows_at_cap_when_buyers_are_turned_away():
    m = Mem(sold=10, received=100, turned=1.8)
    cap, prem, units, why = cashflow.caps(m, 200_000_000, 59_000_000, now=10_000)
    assert why["move"] == "grow" and cap == int(config.STOCK_BUDGET_MAX * config.CAP_GROW)
    assert prem == int(config.PREMIUM_SHARE * cap) and units == int(prem // config.PREMIUM_UNIT_COST)


def test_moves_once_per_step():
    m = Mem(sold=90, received=100)
    first = cashflow.caps(m, 200_000_000, 59_000_000, now=10_000)[0]
    assert cashflow.caps(m, 200_000_000, 74_000_000, now=10_000 + 60)[0] == first


def test_holds_below_cap_and_shrinks_on_slow_sales():
    assert cashflow.caps(Mem(sold=90, received=100, turned=5), 200e6, 20e6, now=10_000)[3]["move"] == "hold"
    cap, _, _, why = cashflow.caps(Mem(sold=10, received=100), 200e6, 20e6, now=10_000)
    assert why["move"] == "shrink" and cap == int(config.STOCK_BUDGET_MAX * config.CAP_SHRINK)


def test_balance_ceiling_wins_and_min_holds_otherwise():
    m = Mem(sold=100, received=100, turned=9)
    m.put("cash_cap", {"cap": 500_000_000, "ts": 0})
    assert cashflow.caps(m, 100_000_000, 60_000_000, now=10_000)[0] == \
        int(config.CAP_CEILING_SHARE * 100_000_000)                                    # 0.60 x balance (0.40 to 11-11)
    assert cashflow.caps(Mem(), 5_000_000, 0, now=10_000)[0] == config.MAX_STOCK_COST    # small shop: old floor
    m = Mem(sold=0, received=100)
    m.put("cash_cap", {"cap": 1, "ts": 0})
    assert cashflow.caps(m, 200_000_000, 0, now=10_000)[0] == config.CAP_MIN


def test_dry_run_keeps_state():
    m = Mem(sold=90, received=100, turned=2)
    cashflow.caps(m, 200e6, 59e6, now=10_000, dry_run=True)
    assert m.get("cash_cap") is None
