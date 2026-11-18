"""tools/student_rank_watch.py (person 11/12 ~16:10): numbers() / text() from a board-shaped dict (students only, our
rank, 9th / 10th gaps, saved vs the best other student) and the 3-hour marks. No network.
    python tests/test_student_rank_watch.py"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, HERE)
import student_rank_watch as W  # noqa: E402


def board():
    pl = [{"player": f"p{i}", "student": True, "student_total": 2.0 - i * 0.01, "student_pct": round(1 - i / 11, 3),
           "buyer": {"name": f"b{i}"}} for i in range(12)]
    pl[10] = {"player": "Choo-chu", "student": True, "student_total": 1.9, "student_pct": 0.09, "buyer": {"name": "choo-chu"}}
    pl[4]["buyer"] = {"name": "11im"}
    pl.append({"player": "outsider", "student_total": 9, "buyer": {"name": "x"}})        # not a student: out
    buyers = [{"name": "choo-chu", "saved": 0.406}, {"name": "b3", "saved": 0.381}, {"name": "b4", "saved": 0.37},
              {"name": "11im", "saved": 0.41},
              {"name": "x", "saved": 0.44}]
    return {"players": pl, "buyers": buyers}


def test_numbers_and_text():
    x = W.numbers(board())
    assert x["rank"] == 11 and x["n"] == 12 and abs(x["ninth"] - 1.92) < 1e-9 and abs(x["tenth"] - 1.91) < 1e-9, x
    assert x["saved_rank"] == 2 and x["second"] == (0.41, "11im") and x["rival"] == 0.41, x
    t = W.text(x, datetime(2026, 11, 12, 18, 0).timestamp())
    assert "11위/12" in t and "9위 1.92 (-0.020)" in t and "10위 1.91 (-0.010)" in t, t
    assert "다음 학생 11im 0.41 (차이 -0.0040)" in t, t
    assert "11im saved 0.41 (차이 -0.0040)" in t, t
    x["grades"] = ({"A": (3, 0.9), "C": (5, 0.8)}, 4.0)
    x["prev_grades"] = ({"B": (2, 0.7)}, 3.0)
    t = W.text(x)
    assert "C등급(첫 제안 0.55, 11/12 23:20~): 최근 3시간 5건 0.800 vs 그 전 3시간 0건" in t, t
    assert "시간당 deals 4.0 vs 3.0" in t, t
    x["rate"], x["prev_rate"] = (29, 100), (41, 100)
    assert "성사율 최근 3시간 29/100 (29%) vs 그 전 3시간 41/100 (41%)" in W.text(x)


def test_due():
    t = lambda h, m: datetime(2026, 11, 12, h, m).timestamp()
    assert W.due(t(18, 5), None) == t(18, 0)
    assert W.due(t(19, 59), t(18, 0)) is None
    assert W.due(t(21, 1), t(18, 0)) == t(21, 0)


if __name__ == "__main__":
    test_numbers_and_text()
    test_due()
    print("ok")
