"""Wider buying B + C (person 11/12 ~14:20), through the operating paths:
  purchasing.make_plan (what restock.main calls): B 6% for a high SKU my shop has sold (an unsold one keeps 8%); C
    my_share 1.2 for a fast hot normal SKU (high price keeps 0.8); the B / C lines go to logs/wide_trial.jsonl, not
    for cap_record's quiet plan; off without wide_trial.json; the guard at the start of the plan turns both off
  tools/wide_trial_watch.step (the watcher's loop): the guard (+10 units / +5,000,000 over the base) and the 3 h report
Live files untouched (temp rules, logs, db, inventory).
    python tests/test_wide_trial.py"""
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
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()
from depts import deep_high, purchasing as PU, top_high, wide_trial as W  # noqa: E402
import wide_trial_watch as WW  # noqa: E402

NOW = time.time()
CAT = "가전>TV"
BASE = {"n": 5, "cost": 5_000_000}


def rules(**k):
    return dict({"enabled": True, "since": round(NOW), "applied": ["C", "B"], "B_min_margin": 0.06, "C_share": 1.2,
                 "base": BASE, "max_add_n": 10, "max_add_cost": 5_000_000, "check_h": 3}, **k)


def catalog():
    return {"H1": {"name": "h1", "list_price": 1_000_000, "kind": "goods", "category": CAT},   # est. 600k, sold before
            "H2": {"name": "h2", "list_price": 1_000_000, "kind": "goods", "category": CAT},   # est. 600k, never sold
            "N1": {"name": "n1", "list_price": 10_000, "kind": "goods", "category": CAT}}      # est. 6,000


def stats():
    return {"skus": {"H1": {"name": "H1", "avg": 642_000, "n": 50, "sold_5m": 1, "sold_30m": 3},   # +7%
                     "H2": {"name": "H2", "avg": 642_000, "n": 50, "sold_5m": 1, "sold_30m": 3},
                     "N1": {"name": "N1", "avg": 7_800, "n": 50, "sold_5m": 3, "sold_30m": 9}},
            "hot": ["N1", "H1", "H2"], "wanted": [], "wanted_cats": [], "sku_break": {}, "sku_n": {}}


def plan_with(wrules, units=(), record=True, sold24=None):
    W.RULES, W.LINES = tmp_json(wrules, "wide_trial"), tmp_path(f"wide_trial-{time.time_ns()}.jsonl")
    PU.HIGH_MARGIN = tmp_json({"enabled": True, "min_margin": 0.08}, "high_margin")
    cat, st = catalog(), stats()
    P = mock.patch.object
    noop = lambda *a, **k: None
    sent = []
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (top_high, "last_costs", lambda *a, **k: {}), (W, "sold_skus", lambda *a, **k: {"H1"}),
                (W, "sold_24h", sold24 or (lambda *a, **k: {"H1"})),
                (PU, "load_stats", lambda: st), (PU, "price_of", lambda k, s=None: (st["skus"][k]["avg"], 50)),
                (PU, "busy_categories", lambda c, top=5: ["TV"]), (PU, "c_rules", lambda: {}),
                (PU, "excluded", lambda: {}), (PU, "guard_check", lambda *a: None), (PU, "sofa_trial", lambda *a: None),
                (PU.sales_report, "policy", lambda: {"high_share": 0.5, "high_max": 100}),
                (deep_high, "current", lambda **k: {}), (PU.decisions, "record", noop),
                (PU.strategy, "current", lambda: ("normal", None))):
            es.enter_context(P(obj, name, val))
        es.enter_context(mock.patch("depts.overcost.mark", lambda units, f, log=None: set()))
        es.enter_context(mock.patch("depts.request_buy.candidates", lambda *a, **k: []))
        es.enter_context(mock.patch("depts.hot_fast.shares", lambda hot, share, log=None: {"N1": 0.8, "H1": 0.8}))
        es.enter_context(mock.patch("depts.high_cost_exclude.current", lambda **k: {}))
        es.enter_context(mock.patch("depts.line_rank.on", lambda *a: False))
        es.enter_context(mock.patch("depts.normal_min.active", lambda *a, **k: None))
        es.enter_context(mock.patch("depts.high_ask.buy_ok", lambda *a: (True, 0, 0)))
        es.enter_context(mock.patch("alerts.send", lambda *a, **k: sent.append(a)))
        logs = []
        units = list(units)
        plan = PU.make_plan({"balance": 10 ** 9, "units": units}, units, cat, 10 ** 9, logs.append, record=record)
    rows = [json.loads(x) for x in open(W.LINES, encoding="utf-8")] if os.path.exists(W.LINES) else []
    return {p["sku"]: p["qty"] for p in plan}, rows, sent, logs


def aged(n, cost=1_000_000):
    return [{"unit": f"a{i}", "sku": "OLD", "cost": cost, "grade": "A", "supplied_at": NOW - 13 * 3600} for i in range(n)]


def test_plan_b_c():
    q, rows, sent, _ = plan_with({})                       # off: 8% keeps H1 out, N1 at 0.8 -> ceil(4.8) = 5
    assert "H1" not in q and "H2" not in q and q["N1"] == 5 and not rows, q
    q, rows, sent, logs = plan_with(rules())               # on: H1 (sold before) at 6%, H2 not; N1 at 1.2 -> ceil(7.2) = 8
    assert q.get("H1") == 1 and "H2" not in q and q["N1"] == 8, q
    assert sorted((r["opt"], r["sku"], r["qty"]) for r in rows) == [("B", "H1", 1), ("C", "N1", 3)], rows
    assert not sent
    _, rows, _, _ = plan_with(rules(), record=False)       # cap_record's quiet plan: nothing logged
    assert not rows
    assert W.c_shares({"N1": 0.8, "H1": 0.8}, lambda k: k == "H1", rules()) == {"N1": 1.2, "H1": 0.8}
    q, rows, sent, _ = plan_with(rules(), units=aged(15))  # 15 aged vs base 5: +10 -> off before the plan, one alert
    assert "H1" not in q and q["N1"] == 5 and not rows, q
    assert len(sent) == 1 and sent[0][0] == "urgent" and sent[0][1].startswith("wide_trial_off"), sent
    assert W.rules()["off_ts"]


def test_watch_guard_and_report():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE my_stock (unit, sku, name, cost, grade, supplied_at, market_avg_at)")
    c.execute("CREATE TABLE settles (ts, shop, sku, amount, unit)")
    c.execute("CREATE TABLE cash (ts, balance, stock_cost)")
    t0 = NOW - 3.1 * 3600
    c.executemany("INSERT INTO my_stock (unit, sku, cost, supplied_at) VALUES (?,?,?,?)",
                  [("n1", "N1", 6_000, t0 + 600), ("n2", "N1", 6_000, t0 - 3600), ("h1", "H1", 600_000, t0 + 900)])
    c.executemany("INSERT INTO settles VALUES (?,?,?,?,?)", [(t0 + 1200, "choo-chu", "N1", 7_800, "n1"),
                                                             (t0 - 1800, "choo-chu", "N1", 7_500, "n2")])
    c.executemany("INSERT INTO cash VALUES (?,?,?)", [(t0, 57_000_000, 82_000_000), (NOW, 55_000_000, 85_000_000)])
    c.commit()
    c.close()
    inv = tmp_json({"units": aged(6)}, "inv")
    path = tmp_json(rules(since=round(t0)), "wide_trial")
    W.LINES = tmp_path("wide_trial-watch.jsonl")
    with open(W.LINES, "w", encoding="utf-8") as f:
        for x in ({"opt": "C", "sku": "N1", "qty": 3, "ts": t0 + 60}, {"opt": "B", "sku": "H1", "qty": 1, "ts": t0 + 60}):
            f.write(json.dumps(x) + "\n")
    sent = []
    kw = dict(db_path=db, inv_path=inv)
    g, rep = WW.step(now=NOW, send=lambda *a: sent.append(a), path=path, units=aged(14, 300_000), **kw)
    assert g == "ok" and rep and len(sent) == 1, (g, sent)     # +9 units, cost 4.2M < base: on; report due (3.1 h)
    txt = sent[0][2]
    assert sent[0][0] == "daily" and "현금 57,000,000 -> 55,000,000 (-2,000,000)" in txt, txt
    assert "C 일반 my_share 1.2: 계획 추가분 3개 (1개 SKU); 그 SKU들 매입 1개 6,000 vs 1개 6,000, 판매 1개 매출 7,800 이익 "            "1,800 vs 1개 7,500 1,500" in txt, txt
    assert "B 고가 마진 6%: 계획 추가분 1개" in txt and "기준: 적용 때 5개 5,000,000 +10개 또는 +5,000,000" in txt, txt
    g, rep = WW.step(now=NOW + 300, send=lambda *a: sent.append(a), path=path, units=aged(14, 300_000), **kw)
    assert g == "ok" and not rep and len(sent) == 1               # the report goes once
    g, _ = WW.step(now=NOW + 600, send=lambda *a: sent.append(a), path=path, units=aged(15, 300_000), **kw)
    assert g == "tripped" and sent[-1][0] == "urgent" and sent[-1][1].startswith("wide_trial_off") and "+10개" in sent[-1][2], sent[-1]
    assert WW.step(now=NOW + 900, send=lambda *a: sent.append(a), path=path, units=aged(30), **kw)[0] == "off"
    assert len(sent) == 2                                        # off: no second alert
    path2 = tmp_json(rules(since=round(t0)), "wide_trial")       # cost: 6 x 1.7M = 10.2M, +5.2M -> off
    assert W.guard(units=aged(6, 1_700_000), now=NOW, send=lambda *a: sent.append(a), path=path2) == "tripped"
    assert W.guard(units=aged(5, 2_000_000), now=NOW, send=lambda *a: None, path=tmp_json(rules(), "w")) == "tripped"  # +5M
    assert W.guard(units=aged(5, 1_999_000), now=NOW, send=lambda *a: None, path=tmp_json(rules(), "w")) == "ok"


def test_two():
    # 11/13 ~00:00: TWO - a high SKU sold in the last 24 h up to 2 (deep_high.limit), via make_plan
    q, rows, _, _ = plan_with(rules(applied=["C", "B", "TWO"]))
    assert q.get("H1") == 2 and "H2" not in q, q
    held = [{"unit": "x", "sku": "H1", "cost": 600_000, "grade": "A", "supplied_at": NOW - 600}]
    q, _, _, _ = plan_with(rules(applied=["C", "B", "TWO"]), units=held)       # one held -> one more
    assert q.get("H1") == 1, q
    q, _, _, _ = plan_with(rules(applied=["C", "B", "TWO"]), units=held * 2)   # two held -> none
    assert "H1" not in q, q
    q, _, _, _ = plan_with(rules())                                            # without TWO: 1 as before
    assert q.get("H1") == 1, q
    assert deep_high.limit("D", {"D": {"limit": 4}}) == 4                      # deep_high's list keeps its limit
    assert W.two_limit("H9", rules(applied=["TWO"]), sold={"H1"}) == 1
    assert W.two_limit("H1", rules(applied=["TWO"], off_ts=1), sold={"H1"}) == 1


def test_two_min_sold():
    # 11/13 ~13:10 (person): TWO only for a high SKU my shop sold >= 2 times in the last 24 h, via make_plan
    import sqlite3
    db = os.path.join(tempfile.mkdtemp(), "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE settles (ts, shop, sku)")
    now = time.time()
    c.executemany("INSERT INTO settles VALUES (?,?,?)", [(now - 60, "choo-chu", "H1")] * 2 + [(now - 60, "choo-chu", "H2")]
                  + [(now - 90000, "choo-chu", "H2")] * 3 + [(now - 60, "other", "H2")] * 3)
    c.commit()
    c.close()
    assert W.sold_24h(db, now, min_n=2) == {"H1"} and W.sold_24h(db, now) == {"H1", "H2"}
    counts = {"H1": 2, "H2": 1}
    s24 = lambda *a, min_n=1, **k: {s for s, n in counts.items() if n >= min_n}
    q, _, _, _ = plan_with(rules(applied=["C", "B", "TWO"], two_min_sold=2), sold24=s24)
    assert q.get("H1") == 2, q                                                     # sold twice: 2
    counts["H1"] = 1
    q, _, _, _ = plan_with(rules(applied=["C", "B", "TWO"], two_min_sold=2), sold24=s24)
    assert q.get("H1") == 1, q                                                     # sold once: back to 1
    q, _, _, _ = plan_with(rules(applied=["C", "B", "TWO"]), sold24=s24)           # without the key: once is enough
    assert q.get("H1") == 2, q


def test_cost_guard():
    # only the 12 h+ unsold high cost over 60M trips: B, C, TWO off, high_max back to 100, one urgent alert
    r = rules(applied=["C", "B", "TWO"], max_cost=60_000_000, high_max_back=100)
    sent, back = [], []
    f = lambda n, why: back.append(n)
    assert W.guard(units=aged(200, 290_000), now=NOW, send=lambda *a: sent.append(a), path=tmp_json(r, "w"),
                   high_max_back_fn=f) == "ok"                                   # 200 units, 58M: the count is no rule
    path = tmp_json(r, "w")
    assert W.guard(units=aged(61), now=NOW, send=lambda *a: sent.append(a), path=path, high_max_back_fn=f) == "tripped"
    assert back == [100] and len(sent) == 1 and sent[0][1].startswith("wide_trial_off"), (back, sent)
    assert "원가 61,000,000 > 기준 60,000,000" in sent[0][2] and "고가 SKU당 1개" in sent[0][2] and "high_max 100" in sent[0][2]
    rr = W.rules(path)
    assert not W.on("TWO", rr) and not W.on("B", rr) and not W.on("C", rr)
    assert W.guard(units=aged(70), now=NOW, send=lambda *a: sent.append(a), path=path, high_max_back_fn=f) == "off"
    # high_max_back writes policy.json and high_caps.json (temp copies)
    import policy as POL
    from depts import high_caps
    with mock.patch.object(POL, "write", lambda sec, why, by="ceo": back.append(sec)),             mock.patch.object(high_caps, "PATH", tmp_path("high_caps.json")):
        high_caps.set_caps(0.85, 200)
        W.high_max_back(100, "t")
        assert high_caps.get() == (0.85, 100) and back[-1] == {"purchasing": {"high_max": 100}}


def test_two_report():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE my_stock (unit, sku, name, cost, grade, supplied_at, market_avg_at)")
    c.execute("CREATE TABLE settles (ts, shop, sku, amount, unit)")
    c.execute("CREATE TABLE cash (ts, balance, stock_cost)")
    t0 = NOW - 3.05 * 3600
    c.executemany("INSERT INTO my_stock (unit, sku, cost, supplied_at) VALUES (?,?,?,?)",
                  [("h1", "H1", 600_000, t0 + 60), ("h2", "H1", 600_000, t0 + 120), ("h0", "H2", 500_000, t0 - 3600),
                   ("n1", "N1", 6_000, t0 + 60)])
    c.executemany("INSERT INTO settles VALUES (?,?,?,?,?)", [(t0 + 1800, "choo-chu", "H1", 650_000, "h1"),
                                                             (t0 - 600, "choo-chu", "H2", 540_000, "h0")])
    c.executemany("INSERT INTO cash VALUES (?,?,?)", [(t0, 78_000_000, 72_000_000), (NOW, 60_000_000, 90_000_000)])
    c.commit()
    c.close()
    path = tmp_json(rules(applied=["C", "B", "TWO"], max_cost=60_000_000, two_since=round(t0), reported=1), "w")
    sent = []
    kw = dict(db_path=db, inv_path=tmp_json({"units": aged(3)}, "inv"), book=lambda a, b: (1_200_000, 15_000_000))
    assert W.report_two_due(now=NOW, send=lambda *a: sent.append(a), path=path, **kw)
    t = sent[0][2]
    assert "고가 매입 2개 1,200,000 vs 1개 500,000" in t and "고가 판매 1개 매출 650,000 이익 +50,000 vs 1개 540,000 +40,000" in t, t
    assert "현금 78,000,000 -> 60,000,000 (-18,000,000)" in t and "끄는 기준: 원가 60,000,000 초과" in t, t
    assert "시간당 Book 1,200,000 vs 1,200,000, 시간당 매출 15,000,000 vs 15,000,000" in t, t
    assert not W.report_two_due(now=NOW + 60, send=lambda *a: sent.append(a), path=path, **kw) and len(sent) == 1


if __name__ == "__main__":
    test_plan_b_c()
    test_watch_guard_and_report()
    test_two()
    test_two_min_sold()
    test_cost_guard()
    test_two_report()
    print("ok")
