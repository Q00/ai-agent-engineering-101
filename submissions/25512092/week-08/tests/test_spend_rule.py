"""CEO spend_share rule (11/09 ~12:15): 0.5 when 6 h profit > 0, stock < STOCK_CAP x 0.9 and cash > 3M (was half the
cap); cash < 2.5M -> 0.3; stock >= 90% of the cap -> no change; else 0.4.
    python tests/test_spend_rule.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts.ceo_policy import spend_rule  # noqa: E402

CAP = 30_000_000


def test_rule():
    assert spend_rule(20_000_000, 25_000_000, 100, CAP)[0] == 0.5        # over half the cap, under 90%: now 0.5
    assert spend_rule(26_999_999, 25_000_000, 100, CAP)[0] == 0.5
    assert spend_rule(27_000_000, 25_000_000, 100, CAP)[0] is None       # within 10% of the cap
    assert spend_rule(10_000_000, 2_400_000, 100, CAP)[0] == 0.3
    assert spend_rule(10_000_000, 2_800_000, 100, CAP)[0] == 0.4         # cash between 2.5M and 3M
    assert spend_rule(10_000_000, 25_000_000, 0, CAP)[0] == 0.4          # no profit
    assert spend_rule(10_000_000, 25_000_000, -5, CAP)[0] == 0.4
    assert spend_rule(17_000_000, 25_000_000, 100, 20_000_000)[0] == 0.5   # 20M cap: 17M < 18M


if __name__ == "__main__":
    test_rule()
    print("ok")
