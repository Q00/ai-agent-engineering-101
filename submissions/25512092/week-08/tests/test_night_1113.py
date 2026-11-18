"""11/13 ~00:05 (person): the 08:00 summary's additions (tools/morning_summary.night_1113) from temp files: student
rank trend from the held student_rank alerts + the board now, the high 1 -> 2 report, cash, C 0.55 by grade, saved
vs 11im, deal rate, deals per hour, the 11/12 revenue line, the decisions needed. No network.
    python tests/test_night_1113.py"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import morning_summary as M  # noqa: E402

T1 = datetime(2026, 11, 13, 8, 0).timestamp()
T0 = T1 - 12 * 3600


def test_night_1113():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE settles (ts, buyer, sku, grade, amount)")
    c.execute("CREATE TABLE buyer_deals (ts, sku, list_price, outcome)")
    c.execute("CREATE TABLE cash (ts, balance, stock_cost)")
    c.executemany("INSERT INTO settles VALUES (?,?,?,?,?)",
                  [(T1 - 3600, "choo-chu", "S1", "C", 5000), (T1 - 7200, "choo-chu", "S1", "A", 9000),
                   (M.C_FROM - 3600, "choo-chu", "S1", "C", 6000)])
    c.executemany("INSERT INTO buyer_deals VALUES (?,?,?,?)", [(T1 - 3600, "S1", 10000, "deal"), (T1 - 3500, "S1", 10000, "walk"),
                                                                (T1 - 7200, "S1", 10000, "deal"), (T1 - 7000, "S1", 10000, "walk")])
    c.executemany("INSERT INTO cash VALUES (?,?,?)", [(T0 - 60, 70_000_000, 80_000_000), (T1 - 60, 60_000_000, 95_000_000)])
    c.commit()
    c.close()
    al = os.path.join(d, "alerts.jsonl")
    with open(al, "w", encoding="utf-8") as f:
        for h, tot, g in ((3, 1.512, "-0.030"), (6, 1.520, "-0.021")):
            t = datetime(2026, 11, 13, h, 0).timestamp()
            f.write(json.dumps({"ts": t, "level": "daily", "key": f"student_rank:{int(t)}", "telegram": "held",
                                "text": f"학생 순위 11-13 0{h}:00: 10위/46 (student_pct 0.8), 합계 {tot}; 9위 1.541 ({g}), 10위"},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"ts": T1 - 4 * 3600, "level": "daily", "key": "high_two_report:1", "telegram": "held",
                            "text": "고가 1→2개 점검\n- 고가 매입 30개"}, ensure_ascii=False) + "\n")
    st = [{"player": f"p{i}", "student": True, "student_total": 2.0 - i * 0.05, "student_pct": 0.9, "buyer": {"name": f"b{i}"}}
          for i in range(10)]
    st[9] = {"player": "Choo-chu", "student": True, "student_total": 1.52, "student_pct": 0.8, "buyer": {"name": "choo-chu"}}
    st[1]["buyer"] = {"name": "11im"}
    board = {"players": st, "buyers": [{"name": "choo-chu", "saved": 0.804}, {"name": "11im", "saved": 0.809}]}
    with mock.patch.object(M, "needs_items", lambda t0, t1: ["x 결정"]), \
            mock.patch("depts.wide_trial.rules", lambda *a: {"off_ts": T1 - 600, "off_why": "원가 61,000,000 > 기준"}), \
            mock.patch("depts.grade_rule.rules", lambda *a: {"cost_share": {"A": 0.65, "B": 0.5, "C": 0.35}}):
        L = M.night_1113(T0, T1, db_path=db, board=board, alerts_path=al)
    t = "\n".join(L)
    assert "03:00 10위 합계 1.512 (9위와 -0.030) -> 06:00 10위 합계 1.520 (9위와 -0.021) -> 지금 10위 합계 1.520" in t, t
    assert "30. 고가 1→2 3시간 점검: 고가 1→2개 점검 / - 고가 매입 30개" in t, t
    assert "70,000,000 (재고 원가 80,000,000)" in t and "60,000,000 (재고 원가 95,000,000)" in t, t
    assert "23:20 이후 A 1건" in t and "C 1건" in t and "vs 18:16~23:20 C 1건" in t, t
    assert "saved 0.804 vs 학생 1위 11im 0.809 (-0.0050)" in t and "성사율 2/4 (50%)" in t, t
    assert "34. 11/12 매출" in t and "35. 결정 필요 목록: x 결정; 고가 안전장치로 B·C·1→2 꺼짐" in t, t
    assert M.rank_parse("학생 순위 11-13 00:00: 10위/46 (student_pct 0.8), 합계 1.509; 9위 1.544 (-0.035), 10위") == \
        (10, 1.509, 1.544, "-0.035")


if __name__ == "__main__":
    test_night_1113()
    print("ok")
