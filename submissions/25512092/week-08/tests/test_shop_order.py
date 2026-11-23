"""depts/shop_order (11/09 ~21:30): no-stock shops back, our 24 h close rate first, < 10 tries -> the median; through
buyer_biz.choose (before the top-3 cut), a failing reorder keeps the shop-score order; no-stock replies logged.
    python tests/test_shop_order.py"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depts import buyer_biz as B, shop_order as S  # noqa: E402
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()                                          # a temp policy.json (code defaults)

S.LOG = tmp_path("no_stock.jsonl")
ON = {"enabled": True, "since": 0, "stock_first": True, "rate_order": True, "min_n": 10}
C = lambda *names: [{"name": n} for n in names]


def test_reorder():
    rate = {"a": (0.2, 50), "b": (0.6, 40), "c": (0.9, 5), "d": (0.4, 30)}      # c: 5 tries -> median 0.4
    out, info = S.reorder(C("a", "b", "c", "d"), lambda c: "G1", r=ON, no_stock=set(), rate=rate)
    assert [c["name"] for c in out] == ["b", "c", "d", "a"] and info["c"] == (False, 0.4, 5)   # ties keep score order
    out, _ = S.reorder(C("a", "b", "c", "d"), lambda c: "G1", r=ON, no_stock={("b", "G1")}, rate=rate)
    assert [c["name"] for c in out] == ["c", "d", "a", "b"]                   # b said no stock for G1: last
    out, _ = S.reorder(C("a", "b"), lambda c: "G1", r=ON, no_stock={("b", "G2")}, rate=rate)
    assert [c["name"] for c in out] == ["b", "a"]                             # another SKU: not pushed back
    out, _ = S.reorder(C("a", "b", "d"), lambda c: "G1", r=dict(ON, rate_order=False), no_stock={("a", "G1")}, rate=rate)
    assert [c["name"] for c in out] == ["b", "d", "a"]                        # stock only: score order otherwise
    out, _ = S.reorder(C("a", "b", "d"), lambda c: "G1", r=dict(ON, stock_first=False), no_stock={("b", "G1")}, rate=rate)
    assert [c["name"] for c in out] == ["b", "d", "a"]                        # rate only
    assert not S.on({"enabled": True, "since": 0, "stock_first": False, "rate_order": False}) and S.on(ON)
    out, _ = S.reorder(C("x", "y"), lambda c: "G1", r=ON, no_stock=set(), rate={})
    assert [c["name"] for c in out] == ["x", "y"]                             # nothing known: unchanged


def test_logs_and_rates():
    now = time.time()
    S.note_no_stock("s1", "G1", now=now - 1800)
    S.note_no_stock("s2", "G2", now=now - 4000)                             # older than 1 h
    assert S.recent_no_stock(now) == {("s1", "G1")}
    db = tmp_path("bd.db")
    d = sqlite3.connect(db)
    d.execute("CREATE TABLE buyer_deals (ts REAL, shop TEXT, outcome TEXT)")
    for i in range(10):
        d.execute("INSERT INTO buyer_deals VALUES (?,?,?)", (now - 60, "s1", "deal" if i < 3 else "fail"))
    d.execute("INSERT INTO buyer_deals VALUES (?,?,?)", (now - 60, "s1", "skip"))              # not counted
    d.execute("INSERT INTO buyer_deals VALUES (?,?,?)", (now - 90000, "s1", "deal"))           # older than 24 h
    d.commit()
    d.close()
    assert S.rates(now, db_path=db) == {"s1": (0.3, 10)}


def test_choose():
    shops = [{"name": n, "url": f"https://{n}", "reviews": []} for n in ("a", "b", "c", "d")]
    scores = {n: {"score": s, "cheap": 0.5, "honest": 1, "success": 0.5} for n, s in zip("abcd", (0.9, 0.8, 0.7, 0.6))}
    plain, _ = B.choose(shops, [], "x", scores)
    assert [c["name"] for c in plain] == ["a", "b", "c"]
    rev = lambda allowed: list(reversed(allowed))
    got, _ = B.choose(shops, [], "x", scores, rev)
    assert [c["name"] for c in got] == ["d", "c", "b"]                        # reordered before the top-3 cut

    def bad(allowed):
        raise ValueError("x")
    got, _ = B.choose(shops, [], "x", scores, bad)
    assert [c["name"] for c in got] == ["a", "b", "c"]                        # failure: shop-score order


def test_no_stock_noted():
    """A shop-ended no-stock reply in the buyer's main loop is written to the no-stock log."""
    import test_second_shop as T
    p = tmp_path("ns2.jsonl")
    old = S.LOG
    S.LOG = p
    try:
        T.run([T.res(ended="seller_ended", end_text="Sorry, none of that in stock."), T.res(deal=True, price=1)],
              d2r={})
    finally:
        S.LOG = old
    rows = [json.loads(x) for x in open(p, encoding="utf-8")]
    assert rows and rows[0]["shop"] == "s0" and rows[0]["sku"] == "GX"


def test_guard():
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import shop_order_check as K
    now = time.time()
    p = tmp_path("sn_guard.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for i in range(120):                                   # last 2 h: 120 orders, 30% close, 18 paid / h
            f.write(json.dumps({"ts": now - 60 * (i % 110) - 30, "deal": i < 36, "paid_ok": i < 36}) + "\n")
    r = dict(ON, since=now - 3 * 3600, base={"rate": 0.40, "dph": 20.0})
    S0 = {"enabled": True, "since": 1, "until": now + 3600}                     # soft_neg on all along (not the live file)
    assert "성사율" in K.guard(now, r, p, sn=S0)                      # 30% < 40% - 5%p
    assert K.guard(now, dict(r, base={"rate": 0.33, "dph": 20.0}), p, sn=S0) is None   # within 5 %p, 18 >= 16
    assert "deals" in K.guard(now, dict(r, base={"rate": 0.30, "dph": 30.0}), p, sn=S0)  # 18 < 24
    assert K.guard(now, dict(r, since=now - 3600), p, sn=S0) is None                     # under 2 h on: not judged
    assert K.guard(now, dict(r, stock_first=False, rate_order=False), p, sn=S0) is None  # already off
    # 11/11 (person): only orders while soft_neg is on, from its last switch-on; >= 100 of them; off -> no judgement
    SN = {"enabled": True, "since": 1, "until": now + 3600}
    assert "성사율" in K.guard(now, r, p, sn=dict(SN, guard_on=now - 3 * 3600))
    assert K.guard(now, r, p, sn=dict(SN, guard_on=now - 1800)) is None             # 30 orders since: held
    assert K.guard(now, r, p, sn=dict(SN, enabled=False)) is None                   # soft_neg off: held
    q = tmp_path("sn_guard2.jsonl")                                                 # 100 since the switch-on: judged
    with open(q, "w", encoding="utf-8") as f:
        for i in range(100):
            f.write(json.dumps({"ts": now - 20 * i - 10, "deal": i < 30, "paid_ok": i < 30}) + "\n")
    assert "성사율" in K.guard(now, r, q, sn=dict(SN, guard_on=now - 2100))
    # the watcher switches only the two rules off
    rules = tmp_path("so_rules.json")
    json.dump(r, open(rules, "w", encoding="utf-8"))
    import saved_guard_watch as W
    from unittest import mock
    sent, rec = [], []
    old_log = S.LOG
    with mock.patch.object(S, "RULES", rules), mock.patch.object(W, "LOG", tmp_path("sg.log")), \
            mock.patch.object(K, "guard", lambda now=None: "성사율 test"):
        W.shop_order_check(mock.Mock(send=lambda *a, **k: sent.append(a), brakes_warn_only=lambda: False),
                           mock.Mock(record=lambda *a, **k: rec.append(a)))
        j = json.load(open(rules, encoding="utf-8"))
    assert not j["stock_first"] and not j["rate_order"] and j["enabled"] and j["stop_reason"] == "성사율 test"
    assert sent and rec and S.LOG == old_log


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
