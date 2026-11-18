"""Buyer break rule (11/09 ~11:45): curtain / rug orders and G81ade83e try warranty shops first; without a warranty
the cap drops to list x (1 - break rate) - 1; with one, the cap is unchanged; other orders untouched.
    python tests/test_buyer_break.py"""
import os
import sqlite3
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import buyer_biz, buyer_break as bb, buyer_speed  # noqa: E402
import json as _json, tempfile as _tf  # noqa: E401,E402

_off = _tf.NamedTemporaryFile("w", suffix=".json", delete=False)
_json.dump({"enabled": False}, _off)
_off.close()
buyer_speed.RULES = _off.name              # live buyer_speed.json is on since 11/09 14:14; the test fakes the shop

R = {"enabled": True, "cats": ["커튼/러그/쿠션/덮개"], "skus": ["G81ade83e"], "default_p": 0.10, "min_n": 30}
CAT = {"G81ade83e": {"category": "가구>커튼/러그/쿠션/덮개", "list_price": 34740},
       "Gx": {"category": "가구>커튼/러그/쿠션/덮개", "list_price": 20000}, "Gk": {"category": "식품>김치", "list_price": 30000}}


def test_basics():
    assert bb.applies("G81ade83e", CAT, R) and bb.applies("Gx", CAT, R) and not bb.applies("Gk", CAT, R)
    assert not bb.applies("Gx", CAT, dict(R, enabled=False))
    assert bb.order_applies("커튼/러그/쿠션/덮개", [], R) and bb.order_applies("x", ["G81ade83e"], R)
    assert not bb.order_applies("김치", ["Gk"], R)
    st = {"sku_break": {"G81ade83e": 0.087, "Gx": 0.5}, "sku_n": {"G81ade83e": 1190, "Gx": 4}}
    assert bb.p_break("G81ade83e", st, R) == 0.087 and bb.p_break("Gx", st, R) == 0.10   # thin -> default
    assert bb.value_cap(34740, 0.087) == int(34740 * 0.913) - 1


def test_warranty_first():
    d = sqlite3.connect(":memory:")
    d.execute("CREATE TABLE settles (shop TEXT, sku TEXT, ts REAL, warranty_hours REAL)")
    import time
    now = time.time()
    for shop, w, n in (("a", 0, 6), ("b", 48, 6), ("c", 48, 2), ("d", 0, 3)):
        for _ in range(n):
            d.execute("INSERT INTO settles VALUES (?,?,?,?)", (shop, "Gx", now - 60, w))
    sh = bb.warranty_shares(d, ["Gx"], now)
    assert sh == {"a": 0.0, "b": 1.0}                              # c, d: too few sales
    order = bb.warranty_first([{"name": "a"}, {"name": "c"}, {"name": "b"}], sh)
    assert [c["name"] for c in order] == ["b", "a", "c"]


class FakeClient:
    """One reply from the shop, then the buyer walks (the harness is mocked)."""
    def __init__(self, text):
        self.text, self.card, self.url, self.sent = text, {"name": "shop", "skills": []}, "u", []

    def send(self, msg, tid=None):
        self.sent.append(msg)
        return {"id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": self.text}]}}}

    def cancel(self, tid):
        pass


def run(text, nw_cap):
    fc = FakeClient(text)
    with mock.patch.object(buyer_biz, "A2AClient", lambda url: fc), \
            mock.patch.object(buyer_biz, "status_text", lambda t: text), \
            mock.patch.object(buyer_biz, "ask_harness", lambda p: {"action": "walk"}), \
            mock.patch.object(buyer_biz.accept_rule, "price", lambda *a, **k: None):
        return buyer_biz.negotiate("u", "커튼", 25000, __import__("time").time(), "I want G81ade83e", min_offers=1,
                                   no_warranty_cap=24000, market_avg=25000)


def test_negotiate_cap():
    r = run("Curtain (G81ade83e), grade A, no warranty; Asking 30000.", 24000)
    assert r.get("break_cap") == 24000, r
    r = run("Curtain (G81ade83e), grade A, 48 hour warranty from our balance; Asking 30000.", 24000)
    assert "break_cap" not in r, r


if __name__ == "__main__":
    test_basics()
    test_warranty_first()
    test_negotiate_cap()
    print("ok")
