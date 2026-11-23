"""Buyer moves by grade for the new saved (person 11/12 ~18:30), through buyer_biz.negotiate with a fake shop (the
buyer's negotiation path; soft moves, no LLM), the shop order helper, and the saved guard's A switch
(tools/saved_guard_watch.a_guard) with temp files:
  A (certificate or "Grade A" in the reply): accept at once <= 1.05 x avg, first offer 0.75 x; B / C / unknown: 1.00 x,
  0.65 x; a shop with >= 20% dishonest claims: one grade lower; a_as_bc: A uses B / C's; no warranty in a break-rule
  SKU: no price whose expected score (list - P) / (list - cost) - 1.8 x p is under 0.65 (none possible -> walk)
    python tests/test_grade_rule.py"""
import json
import os
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
from depts import accept_rule, buyer_biz as B, buyer_speed, grade_rule as G, soft_neg as S  # noqa: E402
from depts import shop_avoid as _SA  # noqa: E402
_SA.EVENTS, _SA.STATE, _SA.MISSED, _SA.RULES = (tmp_path("ev.jsonl"), tmp_path("s.json"), tmp_path("m.jsonl"),
                                                tmp_path("r.json"))
buyer_speed.RULES = tmp_json({"enabled": False}, "bs")
accept_rule.RULES = tmp_json({"enabled": True, "since": 0, "until": None, "low": 0.5, "high": 1.0}, "ar")
accept_rule.LOG = tmp_path("accept_rule.jsonl")
SOFT = {"enabled": True, "since": 0, "until": time.time() + 3600, "share": 1.0, "first": 0.65, "haggles": 2, "accept": 1.0}
S.RULES, S.LOG = tmp_json(SOFT, "sn"), tmp_path("soft_neg.jsonl")
ON = dict(G.DEFAULT, enabled=True)


class Shop:
    ask, grade, warranty = 12000, "A", False
    sent = []

    def __init__(self, url):
        self.card, self.url = {"name": "fake", "skills": []}, url

    def send(self, text, task_id=None):
        Shop.sent.append(text)
        n = int(text) if text.strip().isdigit() else None
        if n is not None and n >= Shop.ask:
            return {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": [{"text": f"Deal at {n}."}]}},
                    "artifacts": [{"parts": [{"data": {"shop": "fake", "item": "G0000000a", "price": n, "task": "t1"}}]}]}
        g = f" Grade {Shop.grade}: normal." if Shop.grade else ""
        w = " 6-hour breakage warranty." if Shop.warranty else ""
        return {"id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                       "message": {"parts": [{"text": f"Item G0000000a.{g}{w} Counteroffer price: {Shop.ask}."}]}}}

    def cancel(self, task_id):
        pass


def run(ask, grade, rules=ON, list_price=20000, p=None, flags=None, warranty=False):
    Shop.ask, Shop.grade, Shop.warranty, Shop.sent = ask, grade, warranty, []
    B.A2AClient = Shop
    B.ask_harness = lambda prompt: (_ for _ in ()).throw(AssertionError("no LLM call expected"))
    with mock.patch.object(B, "SHOP_FLAGS", flags or {}):
        r = B.negotiate("http://fake", "G0000000a", 10500, time.time(), "I want G0000000a", min_offers=1,
                        market_avg=10000, force_first=True, soft=dict(SOFT), shop="fake",
                        grade_ctx={"rules": rules, "list": list_price, "p": p})
    return r, list(Shop.sent)


def test_moves_by_grade():
    r, sent = run(10400, "A")                                   # A: <= 1.05 x avg -> accept at once
    assert r["deal"] and r["grade_used"] == "A" and sent[-1] == "10400", (r, sent)
    r, sent = run(10400, "B")                                   # B: 10400 > 1.00 x avg -> first offer 0.65 x avg
    assert r["grade_used"] == "B" and sent[1] == "6500", sent
    r, sent = run(12000, "A")                                   # A: first offer 0.75 x avg
    assert sent[1] == "7500", sent
    r, sent = run(10400, None)                                  # unknown -> B
    assert r["grade_used"] == "B" and r["grade_claimed"] is None and sent[1] == "6500", sent
    r, sent = run(10400, "A", flags={"fake": {"deals": 100, "dishonest": 30}})   # 30% dishonest: A counted as B
    assert r["grade_claimed"] == "A" and r["grade_used"] == "B" and sent[1] == "6500", (r, sent)
    r, sent = run(10400, "A", flags={"fake": {"deals": 100, "dishonest": 10}})   # 10%: A stays A
    assert r["grade_used"] == "A" and r["deal"]
    r, sent = run(10400, "A", rules=dict(ON, a_as_bc=True))     # the saved guard: A uses B / C's moves
    assert r["grade_used"] == "A" and sent[1] == "6500", sent
    r, sent = run(10400, "A", rules=dict(ON, a_as_bc=True, a_as_bc_locked=True))   # 11/13 locked: A's own moves
    assert r["deal"] and sent[-1] == "10400", sent
    r, sent = run(12000, "A", rules=dict(ON, a_as_bc=True, a_as_bc_locked=True))
    assert sent[1] == "7500", sent


def test_c_first():
    # 11/12 ~23:20: grade C's first offer 0.55 x avg; A / B unchanged; accept for C still 1.00 x
    c = dict(ON, C={"first": 0.55})
    r, sent = run(10400, "C", rules=c)
    assert r["grade_used"] == "C" and sent[1] == "5500", sent
    r, sent = run(10000, "C", rules=c)                          # <= 1.00 x avg -> accept at once
    assert r["deal"] and sent[-1] == "10000", sent
    r, sent = run(10400, "B", rules=c)
    assert sent[1] == "6500", sent
    r, sent = run(12000, "A", rules=c)
    assert sent[1] == "7500", sent
    r, sent = run(10400, "B", rules=c, flags={"fake": {"deals": 100, "dishonest": 30}})   # B counted as C
    assert r["grade_used"] == "C" and sent[1] == "5500", sent
    assert G.moves("C", ON)["first"] == 0.65                    # without the key: BC's


def test_break_cap():
    # list 15000, B (cost 50%), p 0.10: expected >= 0.65 needs P <= 15000 - 0.83 x 7500 = 8775
    r, sent = run(9000, "B", list_price=15000, p=0.10)
    assert r.get("break_cap") == 8775 and not (r["deal"] and r["agreed"] > 8775), r
    r, sent = run(9000, "B", list_price=15000, p=0.10, warranty=True)     # with a warranty: no break cap
    assert r.get("break_cap") is None
    r, sent = run(9000, "B", list_price=15000, p=0.20)                    # 0.65 + 0.36 > 1: no price works -> walk
    assert r["ended"] == "break_score" and not r["deal"], r
    assert G.break_cap(15000, "A", 0.10, ON) == int(15000 - 0.83 * 15000 * 0.35)


def test_shop_order_and_grades():
    import sqlite3
    d = tempfile.mkdtemp()
    db = os.path.join(d, "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE settles (ts, shop, grade)")
    now = time.time()
    c.executemany("INSERT INTO settles VALUES (?,?,?)", [(now - 60, "x", "C")] * 3 + [(now - 60, "x", "A")]
                  + [(now - 60, "y", "A")] * 4 + [(now - 90000, "y", "C")] * 9)
    c.commit()
    c.close()
    share = G.shop_grades(db, now, path=os.path.join(d, "cache.json"))
    assert share == {"x": 0.25, "y": 1.0}, share
    cands = [{"name": "x"}, {"name": "z"}, {"name": "y"}, {"name": "w"}]
    assert [c["name"] for c in G.order_first(cands, 3, share)] == ["y", "z", "x", "w"]   # z unknown 0.5; w stays 4th


def test_shop_scores_order():
    """11/13 ~13:20 (person): shop order by my saved score per deal from the shop (24 h; < 10 deals -> the median),
    through buyer_biz.grade_order (main's call)."""
    import sqlite3
    d = tempfile.mkdtemp()
    db = os.path.join(d, "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE settles (ts, buyer, shop, sku, grade, amount)")
    now = time.time()
    rows = ([(now - 60, "choo-chu", "good", "S1", "A", 6500)] * 10        # A, list 10000: (10000-6500)/3500 = 1.0
            + [(now - 60, "choo-chu", "bad", "S1", "C", 9000)] * 10       # C: (10000-9000)/6500 = 0.1538
            + [(now - 60, "choo-chu", "mid", "S1", "B", 7500)] * 10       # B: 2500/5000 = 0.5
            + [(now - 60, "choo-chu", "few", "S1", "A", 6500)] * 3        # under 10 -> the median 0.5
            + [(now - 90000, "choo-chu", "old", "S1", "A", 6500)] * 20    # outside 24 h
            + [(now - 60, "other", "bad", "S1", "A", 1000)] * 20)         # another buyer: not counted
    c.executemany("INSERT INTO settles VALUES (?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    cat = {"S1": {"list_price": 10000}}
    sc, med = G.shop_scores(db, now, path=os.path.join(d, "sc.json"), catalog=cat)
    assert med == 0.5 and sc == {"good": 1.0, "bad": 0.1538, "mid": 0.5, "few": 0.5}, (sc, med)
    cands = [{"name": n} for n in ("bad", "new", "mid", "good", "few", "x5", "x6")]
    with mock.patch.object(G, "shop_scores", lambda: (sc, med)), mock.patch.object(B, "TRIES_N", 5), \
            mock.patch.object(B, "log", lambda m: None):
        out = B.grade_order(list(cands), dict(ON, shop_key="score"))
        assert [c["name"] for c in out] == ["good", "new", "mid", "few", "bad", "x5", "x6"], out   # new -> median
        assert B.grade_order(list(cands), dict(ON, shop_key="score", enabled=False)) == cands
        with mock.patch.object(G, "shop_grades", lambda: {"bad": 0.9}):                      # without the key: A/B share
            assert B.grade_order(list(cands), dict(ON))[0]["name"] == "bad"


def test_saved_guard_a_switch():
    import saved_guard_watch as W
    path = tmp_json({"enabled": True}, "gr")
    sent = []
    al = mock.Mock(send=lambda *a, **k: sent.append(a))
    dec = mock.Mock(record=lambda *a, **k: None)
    with mock.patch.object(W, "log", lambda m: None):
        assert W.a_guard({"saved": 0.8025, "student_below": 0.7909}, al, dec, path=path) is None   # +0.0116 >= 0.008
        assert W.a_guard({"saved": 0.7980, "student_below": 0.7909}, al, dec, path=path) is True   # +0.0071 < 0.008
        assert G.rules(path)["a_as_bc"] and G.rules(path)["enabled"]
        assert W.a_guard({"saved": 0.8050, "student_below": 0.7909}, al, dec, path=path) is None   # +0.0141 < 0.015
        assert W.a_guard({"saved": 0.8060, "student_below": 0.7909}, al, dec, path=path) is False  # >= 0.015
        assert not G.rules(path)["a_as_bc"]
    assert [a[1].split(":")[0] for a in sent] == ["grade_a_as_bc", "grade_a_as_bc"]
    locked = tmp_json({"enabled": True, "a_as_bc": False, "a_as_bc_locked": True}, "grl")   # 11/13: never on again
    with mock.patch.object(W, "log", lambda m: None):
        assert W.a_guard({"saved": 0.7980, "student_below": 0.7909}, al, dec, path=locked) is None
    assert not G.rules(locked)["a_as_bc"] and len(sent) == 2
    from depts import saved_guard
    assert saved_guard.why("high_accept", {"saved": 0.1}) is None and saved_guard.back("soft_neg", {"saved": 0.9}) is None
    j = {"players": [{"student": True, "buyer": {"name": n}} for n in ("choo-chu", "11im", "dragonash")] + [{"buyer": {"name": "x"}}],
         "buyers": [{"name": "choo-chu", "saved": 0.8025}, {"name": "11im", "saved": 0.8088},
                    {"name": "dragonash", "saved": 0.7909}, {"name": "x", "saved": 0.80}]}
    assert W.student_below(j, 0.8025) == 0.7909                  # the non-student 0.80 is not counted


if __name__ == "__main__":
    test_moves_by_grade()
    test_c_first()
    test_break_cap()
    test_shop_order_and_grades()
    test_shop_scores_order()
    test_saved_guard_a_switch()
    print("ok")
