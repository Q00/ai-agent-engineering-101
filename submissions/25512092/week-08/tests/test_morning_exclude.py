"""tools/morning_summary.exclude_line (11/09 ~19:45): 우아미 소파 G5e3784e6 in buy_exclude.json, read from a temp dir."""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_argv, sys.argv = sys.argv, ["x"]
import morning_summary as M  # noqa: E402
sys.argv = _argv
from _isolate import tmp_path  # noqa: E402


def test_exclude_line():
    old = M.HERE
    d = os.path.dirname(tmp_path("x"))
    try:
        M.HERE = d
        p = os.path.join(d, "buy_exclude.json")
        json.dump({"skus": {"G5e3784e6": "x", "G1": "y"}}, open(p, "w", encoding="utf-8"))
        assert M.exclude_line() == "우아미 소파 G5e3784e6: 매입 제외 목록에 있음 (목록 2개)"
        json.dump({"skus": {"G1": "y"}}, open(p, "w", encoding="utf-8"))
        assert "목록에 없음" in M.exclude_line() and "확인 필요" in M.exclude_line()
        open(p, "w").write("broken")
        assert "읽기 실패" in M.exclude_line()
        os.remove(p)
        assert "읽기 실패 FileNotFoundError" in M.exclude_line()
    finally:
        M.HERE = old


if __name__ == "__main__":
    test_exclude_line()
    print("ok")
