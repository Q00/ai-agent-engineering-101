"""150만 초과 고가 24시간 누적 (person 11/12 ~09:30, for the HIGH_MAX call): tools/check_top15.last24 with a temp db -
bought in the 24 h (by supply time), sold in the 24 h whenever bought, margin, refunds on those sales; and the 08:00 /
20:00 summary's line 27 through morning_summary.summary (other lines faked out). Live files untouched.
    python tests/test_top15_24h.py"""
import os
import sqlite3
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import check_top15 as C  # noqa: E402

T1 = time.time()


def db():
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE my_stock (unit, sku, name, cost, grade, supplied_at, market_avg_at)")
    d.execute("CREATE TABLE settles (seq, ts, buyer, shop_url, shop, sku, name, grade, amount, claim_honest, market_avg_at,"
              " unit, warranty_hours)")
    d.execute("CREATE TABLE broken (ts, shop, sku, buyer, refunded)")
    st = [("u1", "S1", 2_000_000, T1 - 3600), ("u2", "S1", 2_000_000, T1 - 30 * 3600),   # u2 bought before the 24 h
          ("u3", "S2", 1_600_000, T1 - 7200), ("u4", "S3", 1_400_000, T1 - 7200),        # u4 not over 1.5M
          ("u5", "S2", 1_700_000, T1 - 25 * 3600)]
    d.executemany("INSERT INTO my_stock (unit, sku, cost, supplied_at) VALUES (?,?,?,?)", st)
    se = [(T1 - 1800, "b1", "choo-chu", "S1", 2_100_000, "u1"), (T1 - 20 * 3600, "b2", "choo-chu", "S1", 2_200_000, "u2"),
          (T1 - 3000, "b3", "choo-chu", "S3", 1_500_000, "u4"), (T1 - 26 * 3600, "b4", "choo-chu", "S2", 1_800_000, "u5")]
    d.executemany("INSERT INTO settles (ts, buyer, shop, sku, amount, unit) VALUES (?,?,?,?,?,?)", se)
    d.executemany("INSERT INTO broken VALUES (?,?,?,?,?)", [(T1 - 600, "choo-chu", "S1", "b1", 2_100_000),
                                                            (T1 - 600, "choo-chu", "S3", "b3", 1_500_000)])
    d.commit()
    d.close()
    return p


def test_last24():
    x = C.last24(T1, db())
    assert x["bought"] == 2 and x["bought_cost"] == 3_600_000, x                  # u1, u3
    assert x["sold"] == 2 and x["rev"] == 4_300_000 and x["sold_cost"] == 4_000_000, x   # u1, u2 (u4 under, u5 old)
    assert x["profit"] == 300_000 and abs(x["margin"] - 300_000 / 4_300_000) < 1e-9
    assert x["refunds"] == 1 and x["refund_amt"] == 2_100_000, x                  # b3's S3 is not over 1.5M


def test_summary_line_27():
    import morning_summary as M
    p = db()
    real = C.last24
    with mock.patch.object(C, "held_now", lambda state=None: (3, 1)), \
            mock.patch.object(C, "last24", lambda t1, db_path=None, hours=24: real(t1, p)), \
            mock.patch.object(M, "night_lines", lambda t0, t1: ["20. x"]):
        txt = M.summary(T1 - 12 * 3600, T1)
    line = [x for x in txt.splitlines() if x.startswith("27. ")][0]
    assert any(x.startswith("28. 12시간+ 미판매 고가 추이") for x in txt.splitlines()), txt[-500:]
    assert "매입 2개 원가 3,600,000" in line and "판매 2개 매출 4,300,000" in line and "이익률 7.0%" in line, line
    assert "환불 1건 2,100,000" in line and "지금 보유 3개" in line, line


def test_aged_high_trend():
    """Line 28 (person 11/12 ~09:50): high-price units unsold over 12 h, every 3 h over the last 12 h."""
    import json
    import morning_summary as M
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE my_stock (unit, sku, cost, supplied_at)")
    c.execute("CREATE TABLE settles (ts, shop, unit)")
    c.executemany("INSERT INTO my_stock VALUES (?,?,?,?)", [
        ("h1", "A", 200_000, T1 - 30 * 3600),      # held all along
        ("h2", "B", 1_600_000, T1 - 20 * 3600),    # over 12 h at T1 - 6 h and T1 - 3 h, sold at T1 - 2 h
        ("h3", "C", 300_000, T1 - 13 * 3600),      # over 12 h only at T1 (held now)
        ("lo", "D", 50_000, T1 - 30 * 3600),       # not high price
        ("gone", "E", 400_000, T1 - 30 * 3600)])   # neither sold nor held: not counted
    c.executemany("INSERT INTO settles VALUES (?,?,?)", [(T1 - 2 * 3600, "choo-chu", "h2")])
    c.commit()
    c.close()
    inv = os.path.join(d, "inv.json")
    with open(inv, "w", encoding="utf-8") as f:
        json.dump({"units": [{"unit": u, "grade": "A"} for u in ("h1", "h3", "lo")]}, f)
    line = M.aged_high_trend(T1, inv_path=inv, db_path=p)
    parts = line.split(": ", 1)[1].split(" -> ")
    assert [x.split(" ", 1)[1] for x in parts] == ["1개 200,000 (150만 초과 0)", "1개 200,000 (150만 초과 0)",
                                                   "2개 1,800,000 (150만 초과 1)", "2개 1,800,000 (150만 초과 1)",
                                                   "2개 500,000 (150만 초과 0)"], parts


if __name__ == "__main__":
    test_last24()
    test_summary_line_27()
    test_aged_high_trend()
    print("ok")
