"""tools/board_rates.py and accept_extra.orders_lines: orders per hour over a recent window, not since joining."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import board_rates as R  # noqa: E402
import accept_extra as X  # noqa: E402


def B(scale):
    out = [{"name": "choo-chu", "orders": 1000 + 80 * scale, "deals": 300 + 25 * scale, "joined": 0}]
    out += [{"name": f"b{i}", "orders": 5000 + 120 * scale, "deals": 3000 + 80 * scale, "joined": 0} for i in range(10)]
    return out


def test_recent_window():
    p = tempfile.mktemp(suffix=".jsonl")
    R.save(B(0), now=1_000_000, path=p)
    R.save(B(3), now=1_000_000 + 3 * 3600, path=p)
    rt, h = R.rates(B(3), now=1_000_000 + 3 * 3600 + 600, path=p)   # the 10-min-old snapshot is too new
    assert round(h, 2) == 3.17 and round(rt["choo-chu"]["orders"], 1) == round(240 / h, 1)
    lines = X.orders_lines(B(3), B(3)[0], now=1_000_000 + 3 * 3600 + 600, snap=p)
    assert "최근 3.2시간" in lines[0] and "3라운드 누적" in lines[0]


def test_no_snapshot():
    p = tempfile.mktemp(suffix=".jsonl")
    assert R.rates(B(1), path=p) == ({}, 0)
    assert "기록 없음" in X.orders_lines(B(1), B(1)[0], snap=p)[0]


if __name__ == "__main__":
    test_recent_window()
    test_no_snapshot()
    print("ok")
