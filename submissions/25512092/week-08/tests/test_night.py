"""Overnight rules (person's decision 11/09 ~23:55), all on temp files:
cap brake fires on 2 checks in a row (the first a warning); the analyst 3 h check holds with other changes in its
windows and only records before no_revert_until; the night freeze stops the CEO's live policy writes; high_cost_exclude
loss rule needs 3 sales.
    python tests/test_night.py"""
import json
import os
import sys
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()
import limits  # noqa: E402
from depts import cap_brake as CB, night_freeze as NF  # noqa: E402

limits.AUTO_CAP_PATH = tmp_json({"enabled": False}, "auto")


def test_cap_brake_two_in_a_row():
    rules = tmp_json({"enabled": True, "low": 20_000_000, "cash_min": 2_000_000, "window_h": 6, "old_h": 6,
                      "consecutive": 2}, "cb")
    m = {"cash": 10_000_000, "stock": 30_000_000, "sales": 50, "median_h": 5.0, "old_share": 0.1}
    sent, fired = [], []
    seq = [["입고→판매 중앙값 5.0시간 > 4시간"], [], ["입고→판매 중앙값 5.0시간 > 4시간"], ["입고→판매 중앙값 5.0시간 > 4시간"]]
    with mock.patch.object(CB, "RULES", rules), mock.patch.object(CB, "STATE", tmp_path("cbs.json")), \
            mock.patch.object(CB, "end_cap", lambda **k: None), \
            mock.patch("depts.analyst_apply.current_limit", lambda k: 40_000_000), \
            mock.patch.object(CB, "fire", lambda r, m, t, log=None: fired.append(t)), \
            mock.patch("alerts.send", lambda *a, **k: sent.append(a)), mock.patch("decisions.record", lambda *a, **k: None):
        for trips in seq:
            CB._last = 0
            with mock.patch.object(CB, "measure", lambda r, now, t=trips: (dict(m), list(t))):
                CB.tick(log=lambda *_: None)
    assert len(fired) == 1 and len(sent) == 2 and "경고" in sent[0][2], (fired, sent)   # warn, reset, warn, fire


def test_analyst_hold():
    from depts import analyst_apply as AA
    now = time.time()
    p = {"id": 39, "key": "limits.HIGH_MAX", "value": "1500000", "decided_ts": now - 3 * 3600 - 10, "checked": 0,
         "undo": "{}", "kind": "limit"}
    sent, rec, reverted = [], [], []

    def run(mixed, until):
        with mock.patch.object(AA, "HERE", os.path.dirname(tmp_path("x"))),                 mock.patch.object(AA, "analyst_meta", lambda k, d=None: 0), mock.patch.object(AA, "analyst_set", lambda *a: None), \
                mock.patch.object(AA, "_approved", lambda d: [dict(p)]), mock.patch.object(AA, "_cols", lambda d: None), \
                mock.patch.object(AA.analyst, "db", lambda: mock.MagicMock()), \
                mock.patch.object(AA, "mixed_changes", lambda p, a, b: mixed), \
                mock.patch.object(AA, "CHECK_RULES", tmp_json({"no_revert_until": until, "mixed_hold": True}, "ac")), \
                mock.patch("dept_reports.window", lambda *a: {}), \
                mock.patch("depts.ceo_policy.worse", lambda *a, **k: ["매출 변화 -40%p"]), \
                mock.patch("depts.ceo_policy.market_adjusted", lambda *a: (0, 0, 0)), \
                mock.patch("depts.ceo_policy.market_revenue", lambda *a: 0), \
                mock.patch("depts.ceo_policy._catalog", lambda: {}), \
                mock.patch("depts.ceo_policy.trial_units_if_stopped", lambda *a: set()), \
                mock.patch.object(AA, "_auto_revert", lambda p, why: reverted.append(why)), \
                mock.patch("alerts.send", lambda *a, **k: sent.append(a)), \
                mock.patch("decisions.record", lambda *a, **k: rec.append(a)), \
                mock.patch.object(AA.analyst, "log", lambda *a: None):
            with mock.patch.dict("sys.modules"):
                summary_keys = ("sales", "revenue", "profit", "stars", "buyer_paid", "buyer_saved_sum")
                with mock.patch("dept_reports.window", lambda *a: {k: 0 for k in summary_keys}):
                    AA.check_approvals(now)

    run(["21:14 정책 v20"], 0)
    assert not reverted and "판정 보류" in sent[-1][2] and "21:14 정책 v20" in sent[-1][2]
    run([], now + 3600)
    assert not reverted and "판정만 기록" in sent[-1][2]
    run([], 0)
    assert reverted                                                   # nothing mixed, after 09:00: reverted as before


def test_freeze():
    NF.PATH = tmp_json({"until": time.time() + 60}, "nf")
    assert NF.frozen()
    NF.PATH = tmp_json({"until": time.time() - 60}, "nf2")
    assert not NF.frozen()
    NF.PATH = tmp_path("none.json")
    assert not NF.frozen()


def test_loss_needs_three():
    import sqlite3
    from depts import high_cost_exclude as H
    now = time.time()
    p = tmp_path("hce.db")
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, name TEXT, cost INTEGER, supplied_at REAL)")
    db.execute("CREATE TABLE settles (sku TEXT, amount INTEGER, ts REAL, unit TEXT, shop TEXT)")
    for sku, n in (("L2", 2), ("L3", 3)):
        for i in range(n):
            db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?)", (f"{sku}{i}", sku, sku, 300_000, now - 60 * 3600))
            db.execute("INSERT INTO settles VALUES (?,?,?,?,?)", (sku, 290_000, now - 3600, f"{sku}{i}", H.ME))
    db.commit()
    db.close()
    ex = H.compute(now, dict(H.DEFAULT, enabled=True), p)
    assert set(ex) == {"L3"} and ex["L3"]["sold"] == 3, ex


def test_overstock_two_in_a_row():
    from depts import turnover_case as T
    T.STATE = tmp_path("tc_state.json")
    r = {"enabled": True, "since": 0, "case": {"median_h": 4, "old_share": 0.35, "unsold_min": 5_000_000,
                                                "min_cost": 5_000_000, "consecutive": 2}}
    seen = {"m": None}
    sent = []
    bad = {"sales": 50, "median_h": 4.5, "old_cost": 0, "old_unsold": 0, "old_share": None}
    ok = dict(bad, median_h=3.5)                           # 3.5 h: over the old 3 h, under the new 4 h
    unsold = {"sales": 50, "median_h": 1, "old_cost": 12_000_000, "old_unsold": 4_800_000, "old_share": 0.4}  # 4.8M <= 5M
    now = time.time()
    with mock.patch.object(T, "measure", lambda *a: seen["m"]), mock.patch("alerts.send", lambda *a, **k: sent.append(a)),             mock.patch("decisions.record", lambda *a, **k: None):
        seen["m"] = ok
        assert not T.overstock(now, r)[0]
        seen["m"] = unsold
        assert not T.overstock(now + 300, r)[0] and not sent
        seen["m"] = bad
        assert not T.overstock(now + 600, r)[0] and len(sent) == 1 and "경고" in sent[0][2]   # 1/2: warning
        assert not T.overstock(now + 660, r)[0] and len(sent) == 1         # same check window: not counted again
        assert T.overstock(now + 900, r)[0]                                 # 2/2: overstock
        seen["m"] = ok
        assert not T.overstock(now + 1200, r)[0]                            # reset
        seen["m"] = bad
        assert not T.overstock(now + 1500, r)[0] and len(sent) == 2


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
