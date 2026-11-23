"""11/10 ~14:00 (person): the 15:00 check's hourly sales / normal stock and the request-lines verdict
(tools/sales_trend.py). No files.
    python tests/test_sales_trend.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import sales_trend as T  # noqa: E402


def rows(low, units, skus, high=10):
    return [{"t": i, "sales_high": high, "sales_low": l, "low_units": u, "low_skus": k, "ns_none_low": 0,
             "ns_none_high": 0, "ns_lock": 5, "ns_total": 5} for i, (l, u, k) in enumerate(zip(low, units, skus))]


def test_verdict():
    t, p = T.verdict(rows([100, 100, 40, 40], [400, 400, 200, 200], [150, 150, 80, 80]))   # sales -60%, stock -50%
    assert p and "4줄" in t, t
    t, p = T.verdict(rows([100, 100, 40, 40], [400, 400, 300, 300], [150, 150, 140, 140]))  # stock -25%: 42%
    assert p and "3줄" in t, t
    t, p = T.verdict(rows([100, 100, 40, 40], [400, 400, 400, 400], [150, 150, 150, 150]))  # stock flat
    assert not p and "재고 부족 아님" in t, t
    t, p = T.verdict(rows([100, 100, 95, 95], [400, 400, 200, 200], [150, 150, 80, 80]))    # sales -5%
    assert not p and "그대로" in t, t


if __name__ == "__main__":
    test_verdict()
    print("ok")
