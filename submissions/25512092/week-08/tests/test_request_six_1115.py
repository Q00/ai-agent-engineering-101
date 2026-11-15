"""Request-based lines 4 -> 6 (person 11/15 ~14:20: 5 regular + 1 new-SKU trial), through purchasing.make_plan with the
real request_buy.candidates on a temp market.db: 6 lines with request_lines.json lines 6 / trial 1, 3 + 1 with 4;
the guard (12 h+ unsold normal stock > 10M) puts it back to 4 with one urgent Telegram; the 6 h report once.
Live files untouched (temp db, rules, state).
    python tests/test_request_six_1115.py"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy({"purchasing": {"high_max": 120, "high_share": 0.7}})
from depts import deep_high, purchasing as PU, request_buy as RB, request_six as R6, top_high, wide_trial as W  # noqa

NOW = time.time()
REG = [f"R{i}" for i in range(1, 8)]          # 7 fast regular SKUs
NEW = ["N1", "N2", "N3", "N4"]                # no turnover yet: trial


def make_db(d):
    db = sqlite3.connect(os.path.join(d, "market.db"))
    db.execute("CREATE TABLE shop_subst (task TEXT, ts REAL, x TEXT)")
    db.execute("CREATE TABLE settles (seq INTEGER PRIMARY KEY, ts REAL, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER, unit TEXT)")
    db.execute("CREATE TABLE my_stock (unit TEXT PRIMARY KEY, sku TEXT, name TEXT, cost INTEGER, grade TEXT, supplied_at REAL, market_avg_at REAL)")
    for i, sku in enumerate(REG + NEW):
        db.executemany("INSERT INTO shop_subst VALUES (?,?,?)", [(f"t{sku}{k}", NOW - 3600, sku) for k in range(200 - i * 10)])
    for sku in REG:                            # 3 units each sold 0.5 h after supply -> median 0.5 h
        for k in range(3):
            u = f"u-{sku}-{k}"
            db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?,?,?)", (u, sku, sku, 5000, "A", NOW - 5 * 3600, 0))
            db.execute("INSERT INTO settles (ts, buyer, shop, sku, amount, unit) VALUES (?,?,?,?,?,?)",
                       (NOW - 4.5 * 3600, "b", RB.ME, sku, 9000, u))
    db.execute("CREATE TABLE shop_deals (task TEXT, ts REAL, unit TEXT, outcome TEXT, cost INTEGER)")
    db.execute("CREATE TABLE shop_msgs (task TEXT, ts REAL, who TEXT, sku TEXT, text TEXT)")
    # 11/15 ~15:00 items: inextro bought R1 from me 2 h ago (first reply 0.04 s) and Z9 / R2 at nocha12 (R2 held then)
    db.execute("INSERT INTO my_stock VALUES ('u-i', 'R1', 'r1', 5000, 'A', ?, 0)", (NOW - 3 * 3600,))
    db.execute("INSERT INTO settles (ts, buyer, shop, sku, amount, unit) VALUES (?, 'inextro', ?, 'R1', 9000, 'u-i')",
               (NOW - 2 * 3600, RB.ME))
    db.execute("INSERT INTO shop_deals VALUES ('tk', ?, 'u-i', 'receipt', 5000)", (NOW - 2 * 3600 - 30,))
    db.executemany("INSERT INTO shop_msgs VALUES ('tk', ?, ?, 'R1', 'x')", [(NOW - 2 * 3600 - 30, "buyer"),
                                                                            (NOW - 2 * 3600 - 29.96, "shop")])
    db.execute("INSERT INTO my_stock VALUES ('u-h', 'R2', 'r2', 5000, 'A', ?, 0)", (NOW - 3 * 3600,))
    db.executemany("INSERT INTO settles (ts, buyer, shop, sku, amount, unit) VALUES (?, 'inextro', 'nocha12', ?, 9000, ?)",
                   [(NOW - 3600, "R2", "x1"), (NOW - 3600, "Z9", "x2")])
    db.commit()
    db.close()


def catalog():
    c = {k: {"name": k.lower(), "list_price": 10_000, "kind": "goods", "category": "가구>x"} for k in REG + NEW}
    c.update({k: {"name": k.lower(), "list_price": 1_000_000, "kind": "goods", "category": "가전>TV"} for k in ("H1",)})
    return c


def plan(lines, trial, units, rules_extra=None, sent=None):
    d = tempfile.mkdtemp()
    make_db(d)
    st = tmp_json({k: {"first_buy": NOW - 20 * 3600} for k in REG}, f"rbst{time.time_ns()}")
    rl = tmp_json(dict({"lines": lines, "trial": trial, "regular": lines - trial, "base_lines": 4}, **(rules_extra or {})),
                  f"rl{time.time_ns()}")
    deep_high.RULES = tmp_json(dict(deep_high.rules(), enabled=False), "deep_high")
    top_high.RULES = tmp_json({"enabled": False}, "top_high")
    W.RULES, W.LINES = tmp_json({"enabled": False}, "wide_trial"), tmp_path(f"wt-{time.time_ns()}.jsonl")
    PU.HIGH_MARGIN = tmp_json({"enabled": True, "min_margin": 0.08}, "high_margin")
    st_ = {"skus": {}, "hot": [], "wanted": [], "wanted_cats": [], "sku_break": {}, "sku_n": {}}
    sent = [] if sent is None else sent
    P = mock.patch.object
    noop = lambda *a, **k: None
    logs = []
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (RB, "HERE", d), (RB, "STATE", st), (RB, "LINES", rl), (R6, "RULES", rl), (R6, "HERE", d),
                (R6, "PLANS", os.path.join(d, "plans.jsonl")),
                (top_high, "last_costs", lambda *a, **k: {}), (PU, "load_stats", lambda: st_),
                (PU, "price_of", lambda k, s=None: (None, 0)), (PU, "busy_categories", lambda c, top=5: []),
                (PU, "c_rules", lambda: {}), (PU, "excluded", lambda: {}), (PU, "guard_check", lambda *a: None),
                (PU, "sofa_trial", lambda *a: None), (deep_high, "current", lambda **k: {}),
                (PU.decisions, "record", noop), (PU.strategy, "current", lambda: ("normal", None))):
            es.enter_context(P(obj, name, val))
        es.enter_context(mock.patch("market_db.sku_avg", lambda db, sku, now: (20_000, 30)))
        es.enter_context(mock.patch("decisions.record", noop))
        es.enter_context(mock.patch("depts.overcost.mark", lambda units, f, log=None: set()))
        es.enter_context(mock.patch("depts.hot_fast.shares", lambda hot, share, log=None: {}))
        es.enter_context(mock.patch("depts.high_cost_exclude.current", lambda **k: {}))
        es.enter_context(mock.patch("depts.line_rank.on", lambda *a: False))
        es.enter_context(mock.patch("depts.normal_min.active", lambda *a, **k: None))
        es.enter_context(mock.patch("depts.high_ask.buy_ok", lambda *a: (True, 0, 0)))
        es.enter_context(mock.patch("alerts.send", lambda lv, k, t, **kw: sent.append((lv, k, t))))
        es.enter_context(mock.patch.object(R6, "window", lambda t0, t1, db: (10, 100_000, 20_000)))
        p = PU.make_plan({"balance": 10 ** 9, "units": units}, units, catalog(), 10 ** 9, logs.append, record=True)
    req = [m for m in logs if "request-based" in m and "-> buy" in m]
    pl = os.path.join(d, "plans.jsonl")
    plan.rows = [json.loads(x) for x in open(pl, encoding="utf-8")] if os.path.exists(pl) else []
    return p, req, logs, json.load(open(rl, encoding="utf-8")), sent


def aged(cost_each, n):
    return [{"unit": f"a{i}", "sku": f"Z{i}", "cost": cost_each, "grade": "A", "supplied_at": NOW - 13 * 3600} for i in range(n)]


def test_six_lines():
    _, req, logs, r, sent = plan(6, 1, [])
    assert len(req) == 6, (req, logs)
    assert sum("trial" in m for m in req) == 1 and sum("target 8" in m for m in req) == 5, req
    assert not sent and not r.get("off_ts")
    # the plan's request lines are kept: 5 regular + 1 trial, the trial SKU new (no first_buy before)
    row = plan.rows[-1]
    assert row["regular"] == 5 and row["trial"] == 1 and row["new"] == ["N1"], plan.rows
    # 4 lines (as before): 3 regular + 1 trial
    _, req, _, _, _ = plan(4, 1, [])
    assert len(req) == 4 and sum("trial" in m for m in req) == 1, req


def test_three_trials():
    # 11/15 ~15:20 (person): 3 regular + 3 trial: three new SKUs on trial lines (target 2 each), the clock starts
    _, req, logs, r, sent = plan(6, 3, [])
    assert len(req) == 6 and sum("trial" in m for m in req) == 3 and sum("target 8" in m for m in req) == 3, (req, logs)
    row = plan.rows[-1]
    assert row["regular"] == 3 and row["trial"] == 3 and row["new"] == ["N1", "N2", "N3"], plan.rows
    # the guard with 3 trials goes back to 3 + 1, not 1 + 3
    _, _, _, r, sent = plan(6, 3, aged(99_999, 101), {"six_since": NOW - 3600, "max_aged": 10_000_000, "base_trial": 1})
    assert r["lines"] == 4 and r["trial"] == 1 and r["regular"] == 3 and r["six"]["trial"] == 3, r


def test_guard():
    # 12 h+ unsold normal: 101 x 99,999 = 10.1M > 10M -> back to 4, one urgent; this plan already took 6 (guard after)
    _, _, logs, r, sent = plan(6, 1, aged(99_999, 101), {"six_since": NOW - 3600, "max_aged": 10_000_000})
    assert r["lines"] == 4 and r["regular"] == 3 and r.get("off_ts") and r["six"]["lines"] == 6, r
    assert len(sent) == 1 and sent[0][0] == "urgent" and "6줄 자동 원복" in sent[0][2], sent
    assert any("[request six] guard tripped" in m for m in logs), logs
    # under the line: nothing; high-price units do not count
    _, _, _, r, sent = plan(6, 1, aged(99_999, 99) + aged(500_000, 50), {"six_since": NOW - 3600, "max_aged": 10_000_000})
    assert r["lines"] == 6 and not r.get("off_ts") and not sent, (r, sent)


def test_report():
    ex = {"six_since": NOW - 6 * 3600 - 60, "max_aged": 10_000_000, "report_h": 6, "base_aged": [5, 400_000]}
    _, _, logs, r, sent = plan(6, 1, aged(10_000, 3), ex)
    assert r.get("six_reported") and len(sent) == 1 and sent[0][0] == "daily", (r, sent)
    t = sent[0][2]
    assert "요청 기반 매입 6줄 6.0시간 점검" in t and "일반 판매/h 1.7 vs 1.7" in t and "12시간+ 미판매 일반 재고: 지금 3개 30,000 vs 적용 때 5개 400,000" in t, t
    assert "1) 다섯 buyer가 nocha12/nohaggle-hero/25620027/11im에서 산 저가 거래 중 그때 우리 재고에 있던 SKU: 1/2 (50.0%)" in t, t
    assert "2) 다섯 buyer 첫 메시지에 대한 우리 첫 응답(판매자 내부 기록): 중앙 0.04s / 90% 0.04s (n 1)" in t, t
    assert "3) 요청 기반 줄: 계획 1번, 요청 줄 정식 5 / 시험 1 (5+1 다 찬 계획 1번), 새 SKU 1개 (N1)" in t, t
    assert "일반 SKU 수: 지금 3개 vs 기준 184개" in t and "다섯 buyer 우리 몫(시간당 건수) 합 0.2 vs" in t, t
    assert "추가 항목 계산 실패" not in t, t
    # not before 6 h
    _, _, _, r, sent = plan(6, 1, [], dict(ex, six_since=NOW - 5 * 3600))
    assert not r.get("six_reported") and not sent


if __name__ == "__main__":
    test_six_lines()
    test_three_trials()
    test_guard()
    test_report()
    print("ok")
