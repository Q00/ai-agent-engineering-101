"""tools/db_safe_rollout.py (person 11/12 ~15:20): waits for the floor-rule report line, apply() writes db_safe.json
and switches a temp db to WAL, lock_errors() counts per program in a window, report() lines from a temp db.
Live files untouched (the restarts are not run here).
    python tests/test_db_safe_rollout.py"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy  # noqa: E402

policy()
import db_safe_rollout as R  # noqa: E402
import dbutil  # noqa: E402
import market_db  # noqa: E402

D = tempfile.mkdtemp()


def w(name, text):
    p = os.path.join(D, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def test_report_out():
    since = datetime(2026, 11, 12, 17, 59).timestamp()
    assert not R.report_out(w("a.log", "11-12 15:02:28 restarted: report only\n"), since)
    assert R.report_out(w("b.log", "11-12 15:02:28 restarted: report only\n11-12 17:59:55 report sent\n"), since)
    assert R.report_out(w("c.log", "11-12 18:00:01 [alert] urgent loss_rule_report_fail: x\n"), since)
    assert not R.report_out(w("d.log", "11-12 14:00:00 report sent\n"), since)       # an older report


def test_apply_and_report():
    db = os.path.join(D, "market.db")
    with mock.patch.object(market_db, "DB_PATH", db), mock.patch.object(dbutil, "RULES", os.path.join(D, "none.json")):
        market_db.connect().close()
    rules = os.path.join(D, "db_safe.json")
    assert R.apply(rules, db) == "wal" and json.load(open(rules))["enabled"]
    er = os.path.join(D, "end_rules.json")                     # 11/12 ~16:20: lever 1 with the same restarts
    R.apply_end(er)
    fn = os.path.join(D, "food_need.json")
    R.apply_food(fn)
    assert json.load(open(fn))["enabled"] and json.load(open(fn))["margin_h"] == 0.5
    assert json.load(open(er))["restock_stop_h"] == 6 and json.load(open(er))["endgame_h"] == 12
    ev = R.end_values(env=dict(os.environ, W08_END_RULES=er, PYTHONUTF8="1"))
    assert ev["restock_stop_h"] == 6 and ev["stop_at"] == "11-17 17:59" and ev["cap_end_at"] == "11-17 17:59" and ev["endgame_h"] == 12, ev
    t = time.time() - 1800
    c = sqlite3.connect(db)
    c.executemany("INSERT INTO settles (ts, shop, amount) VALUES (?,?,?)", [(t + 60, "choo-chu", 1000),
                                                                          (t - 60, "choo-chu", 500), (t + 70, "x", 9)])
    c.execute("INSERT INTO my_stock (unit, cost, supplied_at) VALUES ('u1', 700, ?)", (t + 100,))
    c.executemany("INSERT INTO buyer_deals (ts, outcome) VALUES (?,?)", [(t + 5, "deal"), (t + 6, "walk")])
    c.commit()
    c.close()
    hm = lambda x: datetime.fromtimestamp(x).strftime("%H:%M:%S")
    kit = w("kit.log", f"{hm(t - 300)} [buyer] [restock] record_cash failed: database is locked\n"
                       f"{hm(t + 300)} [seller] [msg_log] database is locked\n{hm(t + 400)} [buyer] ok\n")
    an = w("an.log", f"{datetime.fromtimestamp(t + 500):%m-%d %H:%M:%S} [buyer_poster] OperationalError: database is locked\n")
    assert R.lock_errors(t, time.time(), kit, an) == {"seller": 1, "analyst": 1}
    txt = R.report(t, db=db, kit=kit, analyst=an)
    assert "journal wal" in txt and "lock 오류: 2건 (analyst 1, seller 1)" in txt, txt
    assert "판매 1건 매출 1,000 vs 직전 같은 길이 1건 500" in txt and "입고 1개 원가 700" in txt, txt
    assert "buyer 주문 2건 중 거래 1건" in txt, txt


if __name__ == "__main__":
    test_report_out()
    test_apply_and_report()
    print("ok")
