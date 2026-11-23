"""tools/check_1113.py (3 h check, person 11/13 ~10:20) with a temp db, a fake board, fake units and a fake sofa_numbers;
then main --once through alerts.send.
    python tests/test_check_1113.py"""
import json
import os
import sqlite3
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import check_1113 as C  # noqa: E402

NOW = 1794542400.0                                      # 11/13 13:00 (sofa units cost > HIGH_MIN count as high too)


def make_db():
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE shop_deals (ts REAL, sku TEXT, cost INTEGER, outcome TEXT, warranty_hours REAL)")
    d.execute("CREATE TABLE settles (ts REAL, shop TEXT, buyer TEXT, sku TEXT, amount INTEGER, unit TEXT, warranty_hours REAL, "
              "grade TEXT)")
    d.execute("CREATE TABLE broken (ts REAL, shop TEXT, sku TEXT, refunded INTEGER)")
    d.executemany("INSERT INTO broken VALUES (?,?,?,?)", [(NOW - 50, "choo-chu", "H1", 600_000),
                                                         (NOW - 50, "choo-chu", "L1", 0)])   # no refund
    d.execute("CREATE TABLE my_stock (unit TEXT, cost INTEGER)")
    d.execute("CREATE TABLE cash (ts REAL, balance INTEGER, stock_cost INTEGER)")
    deals = ([(NOW - 100, "H1", 500_000, "receipt")] + [(NOW - 100, "H1", 500_000, "cancelled")] * 9
             + [(NOW - 200, "SOFA", 300_000, "receipt")] + [(NOW - 200, "SOFA", 300_000, "expired")]
             + [(NOW - 100, "L1", 5_000, "receipt")]                     # low price: not counted
             + [(NOW - 100, "H1", 500_000, "open")]                      # open: not counted
             + [(NOW - 4 * 3600, "H1", 500_000, "receipt")] * 2 + [(NOW - 4 * 3600, "H1", 500_000, "cancelled")] * 2)
    deals = [x + ((48 if x[1] == "H1" and i < 5 else 0),) for i, x in enumerate(deals)]   # H1: 1 receipt + 4 warranty
    d.executemany("INSERT INTO shop_deals VALUES (?,?,?,?,?)", deals)
    d.executemany("INSERT INTO settles (ts, shop, buyer, sku, amount, unit, warranty_hours) VALUES (?,?,?,?,?,?,?)", [
        (NOW - 100, "choo-chu", "inextro", "H1", 600_000, "u1", 48),
        (NOW - 150, "choo-chu", "nocha12", "L1", 6_000, "u2", 0),
        (NOW - 100, "other", "inextro", "H1", 610_000, "x", 48),         # another shop: not counted
        (NOW - 4 * 3600, "choo-chu", "inextro", "H1", 550_000, "u3", 0),
        (NOW - 300, "x", "choo-chu", "B1", 6_500, "b1", 0),                 # my buys (11/13 ~13:10 items 10-13)
        (NOW - 300, "x", "choo-chu", "B1", 9_000, "b2", 0),
        (NOW - 4 * 3600, "x", "choo-chu", "B1", 6_500, "b3", 0)])
    d.execute("UPDATE settles SET grade='A' WHERE unit IN ('b1', 'b3')")
    d.execute("UPDATE settles SET grade='C' WHERE unit='b2'")
    d.executemany("INSERT INTO my_stock VALUES (?,?)", [("u1", 500_000), ("u2", 5_000), ("u3", 500_000)])
    d.executemany("INSERT INTO cash VALUES (?,?,?)", [(NOW - 3 * 3600 - 60, 60_000_000, 90_000_000),
                                                      (NOW - 60, 55_000_000, 95_000_000)])
    d.commit()
    d.close()
    return p


A3 = tmp_json({"enabled": True, "applied_ts": NOW - 7200, "skus": {"H1": {"n": 40, "break": 0.02}}}, "a3")
BOARD = {"players": [{"player": f"p{i}", "student": True, "student_total": 2 - i / 50, "student_shop": 0.7,
                      "student_buyer": 0.8} for i in range(12)]}
BOARD["players"][9]["player"] = "Choo-chu"
for _i, _p in enumerate(BOARD["players"]):
    _p["buyer"] = {"name": "choo-chu" if _i == 9 else f"b{_i}"}
BOARD["buyers"] = [{"name": "choo-chu", "saved": 0.8006}, {"name": "b0", "saved": 0.8097}, {"name": "b1", "saved": 0.7957},
                   {"name": "b2", "saved": 0.70}, {"name": "outsider", "saved": 0.9}]
CAT = {"B1": {"list_price": 10_000}}
BOOK = tmp_json({}, "bf")
with open(BOOK, "w", encoding="utf-8") as _f:
    for _t, _b in ((NOW - 6 * 3600, 100_000_000), (NOW - 3 * 3600, 103_000_000), (NOW, 104_500_000)):
        _f.write(json.dumps({"ts": _t, "s": {"choo-chu": {"book": _b}}, "b": {}}) + "\n")
SCORES = lambda: ({"good": 0.9, "bad": 0.4, "mid": 0.8}, 0.8)


def sofa_numbers(t0, t1):
    return {"revenue": 1_000_000, "profit": 200_000, "refunds": 1, "refund_amount": 300_000}


def test_build():
    p = make_db()
    units = [{"cost": 400_000, "supplied_at": NOW - 13 * 3600, "grade": "A"}, {"cost": 400_000, "supplied_at": NOW - 3600}]
    t = C.build(NOW, db_path=p, board=BOARD, units=units, sofa_numbers=sofa_numbers, is_sofa=lambda s: s == "SOFA",
                a3_path=A3, catalog=CAT, shop_scores=SCORES, board_path=BOOK)
    assert "10. 학생 saved 2위 0.8006: 위 1위 b0 0.8097 (-0.0091), 아래 3위 b1 0.7957 (+0.0049)" in t, t
    assert "11. 등급별 거래·점수(우리 구매): A 1건 1.000 vs 1건 1.000, B 0건 vs 0건, C 1건 0.154 vs 0건" in t, t
    assert "12. 가게별 점수 상위(24시간, 10건 미만은 중앙값 0.8): good 0.9, mid 0.8, bad 0.4" in t, t
    assert "13. Book 시간당: +500,000 vs +1,000,000" in t, t
    assert "고가 2/12 (16.7%) vs 2/4 (50.0%)" in t, t
    assert "소파 1/2 (50.0%)" in t, t
    assert "inextro 1건 600,000 vs 1건" in t and "nocha12 1건 6,000" in t and "cheapest-in-town 0건" in t, t
    assert "고가 판매: 1개 매출 600,000 이익 +100,000 vs 1개 550,000 +50,000" in t, t
    assert "현금: 55,000,000 (재고 원가 95,000,000) vs 3시간 전 60,000,000" in t, t
    assert "12시간+ 미판매 고가: 1개 400,000" in t, t
    assert "10위/12" in t and "9위 p8" in t and "11위 p10" in t and "(+0.020)" in t and "(-0.020)" in t, t
    assert "48시간 환불 300,000 vs 이익 200,000" in t, t
    assert "8. 고가 A등급 보증(3% 목록 1개, " in t, t
    assert "보증 협상 영수증 1/5 (20.0%) vs 0/0; 판매 1개 600,000 vs 0개 0; 환불 1건 600,000 vs 0건 0" in t, t
    assert "9. 구매량 변화" in t and "inextro 0.5 vs 0.0" in t, t
    # 15 (11/13 ~15:20): H1 any warranty since applied (2 h) vs the 2 h before vs the 24 h before x 2/24
    assert ("15. 보증 SKU 1개(보증 유무 무관) 적용 후 2.0시간: 협상 10.0 영수증 1.0 (10.0%) 판매 1.0개 600,000 환불 1.0건 "
            "600,000 | 직전 같은 길이: 협상 4.0 영수증 2.0 (50.0%) 판매 1.0개 550,000 환불 0.0건 0 | "
            "적용 전 24시간을 2.0시간으로 환산: 협상 0.3 영수증 0.2 (50.0%) 판매 0.1개 45,833 환불 0.0건 0") in t, t
    off =C.build(NOW, db_path=p, board=BOARD, units=[], sofa_numbers=sofa_numbers, is_sofa=lambda s: False,
                  a3_path=tmp_json({"enabled": False, "skus": {"H1": {}}}, "a3off"))
    assert "적용 전" in off and "9. 구매량 변화" not in off and "15. 보증 SKU 적용 후 비교: 적용 전" in off, off


def test_score():
    # 17 (11/14 ~16:10): 3 student shops + 1 non-student; s1 passes me in revenue, I pass s2 in stars
    b = dict(BOARD, players=[dict(p, shop={"name": "choo-chu" if p["player"] == "Choo-chu" else f"s{i}"})
                             for i, p in enumerate(BOARD["players"][8:11], 1)])
    b["shops"] = [{"name": "choo-chu", "book": 100, "revenue": 500, "stars_rank": 4.4},
                  {"name": "s1", "book": 200, "revenue": 600, "stars_rank": 4.5},
                  {"name": "s3", "book": 50, "revenue": 100, "stars_rank": 4.3},
                  {"name": "outsider", "book": 999, "revenue": 999, "stars_rank": 5.0}]
    prev = {"choo-chu": {"book": 90, "revenue": 450, "stars_rank": 4.3}, "s1": {"book": 180, "revenue": 400,
            "stars_rank": 4.5}, "s3": {"book": 40, "revenue": 90, "stars_rank": 4.35}}
    bp = tmp_json({}, "bf17")
    with open(bp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": NOW - 3 * 3600 + 120, "s": prev, "b": {}}) + "\n")
    t = C.build(NOW, db_path=make_db(), board=b, units=[], sofa_numbers=sofa_numbers, is_sofa=lambda s: False,
                a3_path=A3, catalog=CAT, shop_scores=SCORES, board_path=bp)
    # now: book 2nd (0.5), revenue 2nd (0.5), stars 2nd (0.5) -> 0.5; before: 0.5 x 0.5 + 0.3 x 1.0 + 0.2 x 0 = 0.55
    assert ("17. shop 점수(학생 3곳 기준) 0.500 vs 3시간 전 0.550: Book 100 2위(0.500) vs 90 2위(0.500); 매출 500 2위(0.500) "
            "vs 450 1위(1.000) [우리를 넘음: s1]; 별점 4.4 2위(0.500) vs 4.3 3위(0.000) [우리가 넘음: s3]") in t, t
    assert "현금·재고 변화: 최근 3시간 현금 -5,000,000 재고 원가 +5,000,000 합 +0 (지금 현금 55,000,000, 재고 원가 95,000,000)" in t, t
    # no board row 3 h ago: now only
    t = C.score_text(NOW, b, make_db(), tmp_json({}, "bf17e"))
    assert "17. shop 점수(학생 3곳 기준) 0.500: Book 100 2위(0.500);" in t, t


def test_deals():
    """11/13 ~18:10: 16. deals per hour (3 h vs 3 h before) and C since the 0.75 rule vs the same length before."""
    p = make_db()
    t = C.build(NOW, db_path=p, board=BOARD, units=[], sofa_numbers=sofa_numbers, is_sofa=lambda s: False,
                a3_path=A3, catalog=CAT, shop_scores=SCORES, board_path=BOOK)
    assert "16. 시간당 deals(우리 구매): 0.7 vs 0.3" in t, t
    cur, prev = C.grade_scores(NOW - 3 * 3600, NOW, p, CAT), C.grade_scores(NOW - 6 * 3600, NOW - 3 * 3600, p, CAT)
    s = C.deals_text(NOW, cur, prev, p, CAT, c_since=NOW - 1800)
    assert s.endswith("C등급 0.75 기준 적용(12:30) 후 0.5시간 1건 0.154 (시간당 2.0) vs 직전 같은 길이 0건"), s
    assert "C등급" not in C.deals_text(NOW, cur, prev, p, CAT, c_since=NOW + 60)


def kit_logs():
    """11/13 ~13:40: two kit logs with HH:MM:SS only - yesterday 23:30 (outside), then 08:00 / 11:00 / 12:30 today."""
    a, b = tmp_json({}, "kit1"), tmp_json({}, "kit2")
    with open(a, "w", encoding="utf-8") as f:
        f.write("23:30:00 [acp] not paying: grade C, the policy accepts ['A', 'B']\n23:59:00 [buyer] x\n")
    with open(b, "w", encoding="utf-8") as f:
        f.write("00:01:00 [buyer] x\n08:00:00 [acp] not paying: grade C, the policy accepts ['A', 'B']\n"
                "11:00:00 [acp] not paying: 162.85 h of shelf life left, the policy asks for 360.0\n"
                "12:30:00 [acp] not paying: grade C, the policy accepts ['A', 'B']\n12:59:00 [acp] paid 100\n")
    return [a, b]


def test_acp():
    t = C.acp_text(NOW, kit_logs())
    assert t == ("14. kit 결제 거절: 2건 vs 1건; grade C, the policy accepts ['A', 'B'] 1 vs 1, "
                 "N h of shelf life left, the policy asks for N 1 vs 0"), t
    assert [round(ts) for ts, _ in C.acp_lines(NOW, kit_logs())] == [NOW - 13.5 * 3600, NOW - 5 * 3600, NOW - 2 * 3600,
                                                                      NOW - 1800]
    t = C.build(NOW, db_path=make_db(), board=BOARD, units=[], sofa_numbers=sofa_numbers, is_sofa=lambda s: False,
                a3_path=A3, catalog=CAT, shop_scores=SCORES, board_path=BOOK, kit_logs=kit_logs())
    assert "14. kit 결제 거절: 2건 vs 1건" in t, t


def test_once():
    p = make_db()
    sent = []
    import alerts
    real = C.build
    with mock.patch.object(C, "build", lambda now: real(NOW, db_path=p, board=BOARD, units=[],
                                                           sofa_numbers=sofa_numbers, is_sofa=lambda s: False, a3_path=A3)), \
            mock.patch.object(alerts, "send", lambda *a, **k: sent.append(a)), \
            mock.patch.object(C.time, "time", return_value=NOW - 3480), mock.patch.object(sys, "argv", ["x", "--once"]):
        C.main()
    assert sent and sent[0][0] == "daily" and sent[0][1] == f"check_1113:{int(NOW - 3600)}", sent


if __name__ == "__main__":
    test_build()
    test_score()
    test_acp()
    test_deals()
    test_once()
    print("ok")
