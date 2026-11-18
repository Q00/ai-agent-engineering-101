"""Analyst approval check against the market (11/09): my revenue change minus the market revenue change < -30%p is
worse; sales / profit drops alone are not; stars still count. #20 (STOCK_CAP 30M, 11/08 20:40) as a case.
    python tests/test_market_adjusted.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import ceo_policy as cp  # noqa: E402


def w(sales, revenue, profit, stars=4.0, acc=10):
    return {"sales": sales, "revenue": revenue, "profit": profit, "stars": stars, "review_accounts": acc,
            "buyer_paid": 0, "buyer_saved_sum": 0, "buyer_saved_avg": None}


def test_rule():
    base = w(100, 10_000_000, 2_000_000)
    # market fell 40%, I fell 50%: -10%p -> not worse (old rule: sales 50 < 70 -> worse)
    cur = w(50, 5_000_000, 1_000_000)
    assert cp.worse(cur, base, min_sales=10, buyer=False, market=(60, 100)) == []
    assert cp.worse(cur, base, min_sales=10, buyer=False)          # the old rule still for the CEO guard
    # market flat, I fell 35% -> worse
    bad = cp.worse(w(65, 6_500_000, 1_900_000), base, min_sales=10, buyer=False, market=(100, 100))
    assert len(bad) == 1 and "-35.0%p" in bad[0], bad
    # stars still count
    assert cp.worse(w(100, 10_000_000, 2_000_000, stars=3.0), base, min_sales=10, buyer=False, market=(100, 100))
    # too few sales in the base: no revenue judgement
    assert cp.worse(w(1, 0, 0), w(5, 1_000_000, 100_000), min_sales=10, buyer=False, market=(100, 100)) == []


def test_case_20():
    before, after = w(335, 17_911_713, 2_901_622, 4.05, 35), w(204, 7_444_640, 1_507_849, 4.09, 31)
    mine, mkt, adj = cp.market_adjusted(after, before, 1_747_640_973, 1_776_250_971)
    assert round(mine, 3) == -0.584 and round(mkt, 3) == -0.016 and round(adj, 3) == -0.568
    assert cp.worse(after, before, min_sales=10, min_accounts=5, buyer=False, market=(1_747_640_973, 1_776_250_971))


if __name__ == "__main__":
    test_rule()
    test_case_20()
    print("ok")
