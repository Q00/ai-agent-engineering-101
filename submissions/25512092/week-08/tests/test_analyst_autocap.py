"""11/11 ~14:05 (person): the analyst knows the automatic STOCK_CAP (cash + stock - reserve) and proposes no STOCK_CAP
change while it is on; an approval of one is refused.
    python tests/test_analyst_autocap.py"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_path  # noqa: E402

policy()
import limits  # noqa: E402
from depts import analyst_apply, analyst_brain  # noqa: E402


def cfg(enabled):
    p = tmp_path(f"auto_{enabled}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"enabled": enabled, "reserve": 5_000_000, "exit_ratio": 0.88}, f)
    return p


def test_auto_on():
    with mock.patch.object(limits, "AUTO_CAP_PATH", cfg(True)):
        assert "limits.STOCK_CAP" not in analyst_brain.allowed_keys()
        assert limits.stock_cap_now(40_000_000, 60_000_000) == (95_000_000, 83_600_000, "auto")
        good, dropped = analyst_brain.validate('{"proposals": [{"key": "limits.STOCK_CAP", "value": 30000000, '
                                               '"reason": "x", "evidence": "y"}]}')
        assert not good and dropped
        try:
            analyst_apply.check_max("limits.STOCK_CAP", 30_000_000)
            raise AssertionError("not refused")
        except ValueError as e:
            assert "automatic" in str(e)


def test_auto_off():
    with mock.patch.object(limits, "AUTO_CAP_PATH", cfg(False)):
        assert "limits.STOCK_CAP" in analyst_brain.allowed_keys()
        analyst_apply.check_max("limits.STOCK_CAP", 30_000_000)


if __name__ == "__main__":
    test_auto_on()
    test_auto_off()
    print("ok")
