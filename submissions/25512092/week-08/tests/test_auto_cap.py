"""Automatic STOCK_CAP (person's decision 11/09 ~23:10): cap = cash + stock - 3M every run, exit 88%; the cap brake's
stock trips turn it off (fixed 20M), its cash trip only pauses buying until cash > 3M; the 11/15 end cap keeps priority.
Request-based buying: 2 lines, only SKUs with a supply -> sale median of 1 h or less. All on temp files.
    python tests/test_auto_cap.py"""
import json
import os
import sqlite3
import sys
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()
import limits  # noqa: E402
from depts import cap_brake as CB, ceo  # noqa: E402

ON = {"enabled": True, "reserve": 3_000_000, "exit_ratio": 0.88, "resume_cash": 3_000_000, "paused": False}


def auto(obj):
    limits.AUTO_CAP_PATH = tmp_json(obj, "auto")
    return limits.AUTO_CAP_PATH


def test_cap_now():
    auto(ON)
    now = limits.CAP_END_AT - 86400
    assert limits.stock_cap_now(14_000_000, 35_000_000, now) == (46_000_000, int(46_000_000 * 0.88), "auto")
    assert limits.stock_cap_now(14_000_000, 35_000_000, limits.CAP_END_AT + 1)[2] == "fixed"   # the end cap first
    auto(dict(ON, enabled=False))
    assert limits.stock_cap_now(14_000_000, 35_000_000, now) == (limits.STOCK_CAP, limits.STOCK_EXIT, "fixed")
    auto(dict(ON, paused=True))
    assert limits.buy_paused(now=now) and not limits.buy_paused(now=limits.CAP_END_AT + 1)


def test_case():
    auto(ON)
    now = limits.CAP_END_AT - 86400
    inv = lambda cash, stock: {"balance": cash, "units": [{"cost": stock, "grade": "A"}]}
    from depts import turnover_case as T
    over = {"v": (False, "turnover ok")}
    with mock.patch.object(ceo.pricing, "endgame_step", lambda t: None), \
            mock.patch.object(T, "overstock", lambda now=None: over["v"]):
        # 11/09 ~23:40: with the automatic cap, overstock by turnover only; stock >= cap or low cash is not
        assert ceo.raw_shop_case(inv(14_000_000, 35_000_000), now)[0] == "normal"
        assert ceo.raw_shop_case(inv(2_900_000, 35_000_000), now)[0] == "normal"          # cash <= 3M: budget 0 only
        assert ceo.raw_shop_case(inv(500_000, 35_000_000), now)[0] == "normal"            # not cash_low either
        over["v"] = (True, "turnover: sale median 3.5 h > 3 h")
        c, why = ceo.raw_shop_case(inv(14_000_000, 10_000_000), now)
        assert c == "overstock" and "median" in why
        auto(dict(ON, enabled=False))
        over["v"] = (False, "")
        assert ceo.raw_shop_case(inv(14_000_000, 35_000_000), now)[0] == "overstock"       # fixed 20M
        assert ceo.raw_shop_case(inv(500_000, 10_000_000), now)[0] == "cash_low"


def test_turnover_case():
    from depts import turnover_case as T
    now = time.time()
    p = tmp_path("tc.db")
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, cost INTEGER, supplied_at REAL)")
    db.execute("CREATE TABLE settles (unit TEXT, shop TEXT, ts REAL)")
    for i in range(12):                                   # 12 sales in 6 h, 2 h from supply to sale
        db.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (f"s{i}", "A", 100_000, now - 4 * 3600))
        db.execute("INSERT INTO settles VALUES (?,?,?)", (f"s{i}", T.ME, now - 2 * 3600))
    for i in range(10):                                   # 7 h old: 4 of 10 x 600k unsold = 40% of 6M
        db.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (f"o{i}", "B", 600_000, now - 7 * 3600))
        if i >= 4:
            db.execute("INSERT INTO settles VALUES (?,?,?)", (f"o{i}", T.ME, now - 3600))
    db.commit()
    db.close()
    m = T.measure(now, 0, None, p)
    assert m["sales"] == 18 and m["old_cost"] == 6_000_000 and m["old_share"] == 0.4, m
    assert T.overstock(now, dict(ON, since=0), p)[0]                                      # 40% > 25%
    assert not T.overstock(now, dict(ON, since=0, case={"old_share": 0.5}), p)[0]
    assert not T.overstock(now, dict(ON, since=now - 3600), p)[0]                         # supplied before the switch-on
    assert T.overstock(now, dict(ON, since=0, case={"old_share": 0.5, "median_h": 1}), p)[0]   # median 2 h > 1 h


def brake(m, trips, auto_obj, fixed=20_000_000):
    auto(auto_obj)
    rules = tmp_json({"enabled": True, "low": 20_000_000, "cash_min": 2_000_000, "window_h": 6, "old_h": 6}, "cb")
    sent, rec = [], []
    CB._last = 0
    with mock.patch.object(CB, "RULES", rules), mock.patch.object(CB, "STATE", tmp_path("cbs.json")), \
            mock.patch.object(CB, "measure", lambda r, now: (dict(m), list(trips))), \
            mock.patch.object(CB, "end_cap", lambda **k: None), \
            mock.patch("depts.analyst_apply.current_limit", lambda k: fixed), \
            mock.patch("depts.analyst_apply.edit_limit", side_effect=AssertionError("no edit at 20M")), \
            mock.patch("alerts.send", lambda *a, **k: sent.append(a)), \
            mock.patch("decisions.record", lambda *a, **k: rec.append(a)):
        CB.tick(log=lambda *_: None, now=limits.CAP_END_AT - 86400)
    return json.load(open(limits.AUTO_CAP_PATH, encoding="utf-8")), json.load(open(rules, encoding="utf-8")), sent


M = {"cash": 10_000_000, "stock": 30_000_000, "sales": 50, "median_h": 4.0, "old_share": 0.1}


def test_brake_stock_trip_turns_auto_off():
    a, r, sent = brake(M, ["입고→판매 중앙값 4.0시간 > 3시간"], ON)
    assert not a["enabled"] and a["fired"] and not r["enabled"] and sent and "끄고 20,000,000 복귀" in sent[0][2]


def test_brake_cash_pauses_then_resumes():
    a, r, sent = brake(dict(M, cash=1_500_000), ["현금 1,500,000 < 2,000,000"], ON)
    assert a["enabled"] and a["paused"] and r["enabled"] and "매입만 중지" in sent[0][2]
    a, r, sent = brake(dict(M, cash=2_500_000), [], a)                    # 2.5M: still paused
    assert a["paused"] and not sent
    a, r, sent = brake(dict(M, cash=3_100_000), [], a)                    # over 3M: resumed
    assert a["enabled"] and not a["paused"] and sent


def test_brake_cash_and_stock_trip():
    a, r, sent = brake(dict(M, cash=1_500_000), ["현금 1,500,000 < 2,000,000", "상향 후 입고분 중 6시간+ 미판매 비율 30%"], ON)
    assert not a["enabled"]                                                 # a stock trip wins: auto off, 20M


def test_end_cap_turns_auto_off():
    auto(ON)
    with mock.patch("depts.analyst_apply.current_limit", lambda k: 20_000_000), \
            mock.patch("depts.analyst_apply.edit_limit", lambda *a: {"limits.py": ""}), \
            mock.patch("depts.analyst_apply.git_commit", lambda *a: "abc"), \
            mock.patch.object(CB, "RULES", tmp_json({"enabled": True}, "cb2")), \
            mock.patch("alerts.send", lambda *a, **k: None), mock.patch("decisions.record", lambda *a, **k: None):
        CB.end_cap(log=lambda *_: None, now=limits.CAP_END_AT + 1)
    assert not json.load(open(limits.AUTO_CAP_PATH, encoding="utf-8"))["enabled"]


def test_request_fast_only():
    from depts import request_buy as rb
    d = tmp_path("rb")
    os.makedirs(d, exist_ok=True)
    now = time.time()
    db = sqlite3.connect(os.path.join(d, "market.db"))
    db.execute("CREATE TABLE shop_subst (ts REAL, x TEXT)")
    db.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, cost INTEGER, supplied_at REAL)")
    db.execute("CREATE TABLE settles (unit TEXT, shop TEXT, ts REAL, sku TEXT)")
    for sku in ("F1", "F2", "F3", "S1", "N1"):
        db.executemany("INSERT INTO shop_subst VALUES (?,?)", [(now - 60, sku)] * 60)
    for sku, h in (("F1", 0.5), ("F2", 0.7), ("F3", 0.9), ("S1", 3.0)):      # N1: never bought, no turnover
        db.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (sku + "u", sku, 1000, now - 5 * 3600))
        db.execute("INSERT INTO settles VALUES (?,?,?,?)", (sku + "u", rb.ME, now - 5 * 3600 + h * 3600, sku))
    db.commit()
    db.close()
    st = tmp_json({s: {"first_buy": now - 6 * 3600} for s in ("F1", "F2", "F3", "S1")}, "rbst")
    cat = {s: {"list_price": 10_000, "kind": "goods"} for s in ("F1", "F2", "F3", "S1", "N1")}
    rec = []
    with mock.patch.object(rb, "HERE", d), mock.patch.object(rb, "STATE", st), \
            mock.patch("market_db.sku_avg", lambda db, sku, now: (20_000, 10)), \
            mock.patch("decisions.record", lambda *a, **k: rec.append(a)):
        out = rb.candidates(cat, {}, {}, log=lambda *_: None, now=now)
        # 11/09 ~23:40: 3 regular (fast, target 8; 11/10 2 -> 4 lines) + 1 trial of the new SKU N1 (target 2)
        assert [s for s, *_ in out] == ["F1", "F2", "F3", "N1"] and out[0][1] == rb.FAST_TARGET, out   # 11/10: 3 + 1 trial
        assert out[3][1] == rb.TRIAL_TARGET
        rb.bought(["N1"], now=now)
        j = json.load(open(st, encoding="utf-8"))
        assert j["N1"]["trial"] == round(now) and "trial_pending" not in j["N1"]
        out = rb.candidates(cat, {"N1": 2}, {}, log=lambda *_: None, now=now + 60)       # trial bought up: keeps
        assert len(out) == 3 and "N1" not in [s for s, *_ in out], out                   # its slot kept, 3 regular lines
        # 6 h later: N1 sold within 0.5 h -> regular
        db = sqlite3.connect(os.path.join(d, "market.db"))
        db.execute("INSERT INTO my_stock VALUES (?,?,?,?)", ("N1u", "N1", 1000, now + 120))
        db.execute("INSERT INTO settles VALUES (?,?,?,?)", ("N1u", rb.ME, now + 120 + 1800, "N1"))
        db.commit()
        db.close()
        rb.candidates(cat, {"N1": 2}, {}, log=lambda *_: None, now=now + 6 * 3600 + 60)
        j = json.load(open(st, encoding="utf-8"))
        assert j["N1"].get("trial_ok") and not j["N1"].get("dropped")
        # a slow trial is dropped
        j["S2"] = {"first_buy": now - 7 * 3600, "trial": now - 7 * 3600}
        json.dump(j, open(st, "w", encoding="utf-8"))
        db = sqlite3.connect(os.path.join(d, "market.db"))
        db.execute("INSERT INTO my_stock VALUES (?,?,?,?)", ("S2u", "S2", 1000, now - 6.5 * 3600))
        db.commit()
        db.close()
        rb.candidates(cat, {}, {}, log=lambda *_: None, now=now)
        j = json.load(open(st, encoding="utf-8"))
        assert j["S2"]["dropped_why"] == "trial"
    assert any(a[1] == "request_trial_drop" for a in rec) and any(a[1] == "request_trial_ok" for a in rec)


def test_high_cost_exclude():
    from depts import high_cost_exclude as H
    now = time.time()
    p = tmp_path("hce.db")
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, name TEXT, cost INTEGER, supplied_at REAL)")
    db.execute("CREATE TABLE settles (sku TEXT, amount INTEGER, ts REAL, unit TEXT, shop TEXT)")
    # 11/09 ~23:50: cost rule needs 3+ units bought in 48 h; or the 48 h sales profit adds up below zero
    for sku, cost, avg, n in (("HI", 500_000, 510_000, 3), ("HI1", 500_000, 510_000, 1), ("OK", 400_000, 500_000, 3),
                              ("LOW", 50_000, 50_000, 5)):
        for i in range(n):
            db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?)", (f"{sku}u{i}", sku, sku, cost, now - 3600))
        db.executemany("INSERT INTO settles VALUES (?,?,?,?,?)", [(sku, avg, now - 600, None, "x")] * 10)
    db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?)", ("OLDu", "OLD", "OLD", 600_000, now - 50 * 3600))
    db.executemany("INSERT INTO settles VALUES (?,?,?,?,?)", [("OLD", 500_000, now - 600, None, "x")] * 10)
    db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?)", ("LSu1", "LS", "LS", 300_000, now - 50 * 3600))
    db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?)", ("LSu2", "LS", "LS", 300_000, now - 50 * 3600))
    db.execute("INSERT INTO settles VALUES (?,?,?,?,?)", ("LS", 250_000, now - 3600, "LSu1", H.ME))      # -50k
    db.execute("INSERT INTO settles VALUES (?,?,?,?,?)", ("LS", 320_000, now - 3600, "LSu2", H.ME))      # +20k
    db.execute("INSERT INTO settles VALUES (?,?,?,?,?)", ("LS", 900_000, now - 3600, "LSu2", "other"))   # not mine
    db.commit()
    db.close()
    r = dict(H.DEFAULT, enabled=True, force=["FX"], min_sold=2)   # the loss rule needs 3 sales from 11/09 ~23:55
    ex = H.compute(now, r, p)
    assert set(ex) == {"HI", "LS", "FX"} and ex["HI"]["ratio"] == 0.98 and ex["FX"]["why"] == "force", ex
    assert ex["LS"] == {"profit": -30_000, "sold": 2, "name": "LS", "why": "loss"}, ex["LS"]
    assert H.compute(now, dict(r, enabled=False), p) == {}
    H.OUT, H.RULES = tmp_path("hce_out.json"), tmp_json(r, "hce_rules")
    with mock.patch.object(H, "compute", lambda now=None: ex):
        H.current()
    assert "3개" in H.line() and "HI" in H.line() and "손실 -30,000" in H.line()


def test_revert_confirm():
    """11/09 ~23:40: a 되돌리기 press only asks; [정말 되돌리기] runs it, [취소] changes nothing."""
    from depts import analyst as A
    calls, ran = [], []
    cq = lambda data: {"id": "c", "data": data, "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 9, "text": "x"}}
    with mock.patch.object(A, "_call", lambda m, **k: calls.append((m, k))), mock.patch.object(A, "chat_id", lambda: 1), \
            mock.patch("decisions.record", lambda *a, **k: None), mock.patch.object(A, "log", lambda *a: None), \
            mock.patch.object(A, "db", side_effect=AssertionError("nothing read before the confirmation")):
        assert A.handle_callback(cq("revert:5")) == "confirm asked"
        assert A.handle_callback(cq("undo:6")) == "confirm asked"
        assert A.handle_callback(cq("cancel_undo:6")) == "cancelled"
    asks = [k for m, k in calls if m == "sendMessage"]
    assert len(asks) == 2 and "정말 되돌릴까요" in asks[0]["text"]
    assert asks[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "revert_ok:5"
    assert asks[1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "cancel_undo:6"
    # confirmed: goes on to the revert itself
    row = {"id": 5, "kind": "limit", "status": "approved", "key": "limits.STOCK_CAP", "value": "1", "reason": "",
           "message_id": None, "expires": 0, "asked": 0}
    d = mock.Mock()
    d.execute.return_value.fetchone.return_value = row
    with mock.patch.object(A, "_call", lambda m, **k: calls.append((m, k))), mock.patch.object(A, "chat_id", lambda: 1), \
            mock.patch("decisions.record", lambda *a, **k: None), mock.patch.object(A, "log", lambda *a: None), \
            mock.patch.object(A, "db", lambda: d), mock.patch.object(A, "REVERT", lambda p: ran.append(p) or "ok"), \
            mock.patch.object(A, "_finish", lambda p, s, r, c: ran.append(s)):
        assert A.handle_callback(cq("revert_ok:5")) == "revert"
    assert ran[-1] == "reverted"


def test_revert_attribution_and_brake():
    """11/09 ~23:50: an automatic 3 h check revert is recorded as the check's, not "by the person"; a STOCK_CAP raise
    the cap brake governs is only recorded by the generic check."""
    from depts import analyst_apply as AA, cap_brake as CB2
    seen = []
    p = {"id": 7, "key": "limits.STOCK_CAP", "value": "40000000", "undo": json.dumps({"limits.STOCK_CAP": 20_000_000})}
    with mock.patch.object(AA, "apply_limit", lambda k, v, pid, why="": seen.append(why) or {"text": "t"}), \
            mock.patch("decisions.record", lambda *a, **k: None):
        AA.revert(p)
        AA.revert(p, by="the 3 h check (automatic)")
    assert seen == ["reverted by the person", "reverted by the 3 h check (automatic)"]
    with mock.patch.object(CB2, "RULES", tmp_json({"enabled": True, "pid": 7}, "cbg")):
        assert AA._brake_governs(p) and not AA._brake_governs(dict(p, id=8))
        assert not AA._brake_governs(dict(p, key="limits.HIGH_MAX"))
    with mock.patch.object(CB2, "RULES", tmp_json({"enabled": False, "pid": 7}, "cbg2")):
        assert not AA._brake_governs(p)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
