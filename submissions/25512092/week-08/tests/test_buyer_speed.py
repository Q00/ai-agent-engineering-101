"""Buyer speed (11/09 ~11:55): the opening offer is the same with and without the skipped first harness call; the
card cache; the 10 s first-reply limit raises SlowShop.
    python tests/test_buyer_speed.py"""
import os
import sys
import tempfile
from unittest import mock

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import buyer_biz, buyer_speed as bs  # noqa: E402

ON = {"enabled": True, "skip_first_llm": True, "card_ttl_s": 600, "first_reply_s": 10}
OFF = dict(ON, enabled=False)


class FakeClient:
    """Asks `ask`, counters once, then ends without a deal."""
    def __init__(self, ask):
        self.ask, self.sent, self.card, self.url = ask, [], {"name": "shop", "skills": []}, "u"
        self.http = mock.Mock(timeout=120)

    def send(self, msg, tid=None):
        self.sent.append(msg)
        st = "TASK_STATE_INPUT_REQUIRED" if len(self.sent) < 3 else "TASK_STATE_CANCELED"
        txt = f"Item X, grade A, no warranty. Asking {self.ask}." if len(self.sent) == 1 else f"Counteroffer: {self.ask - 1000}."
        return {"id": "t1", "status": {"state": st, "message": {"parts": [{"text": txt}]}}}

    def cancel(self, tid):
        pass


def run(rules, ask, first_offer, budget, harness_price):
    fc, calls = FakeClient(ask), []

    def harness(p):
        calls.append(p)
        return {"action": "offer", "price": harness_price}
    with mock.patch.object(bs, "rules", return_value=rules), mock.patch.object(bs, "client", lambda *a, **k: fc), \
            mock.patch.object(buyer_biz, "ask_harness", harness), \
            mock.patch.object(buyer_biz.accept_rule, "price", lambda *a, **k: None):
        r = buyer_biz.negotiate("u", "x", budget, __import__("time").time(), "I want X (Gx)", min_offers=2,
                                first_offer=first_offer, force_first=True, market_avg=budget)
    return fc.sent, calls, r


def test_same_first_offer():
    for ask, fo, budget, hp in ((30000, 0.5, 25000, 11111), (21734, 0.6, 19585, 17000), (9000, 0.7, 5000, 4000),
                                (100, 0.5, 1000, 3)):
        s_off, c_off, r_off = run(OFF, ask, fo, budget, hp)
        s_on, c_on, r_on = run(ON, ask, fo, budget, hp)
        assert s_off[1] == s_on[1], (ask, fo, budget, s_off, s_on)          # the opening offer, word for word
        assert s_on[1].split(".")[0] == str(max(1, min(int(ask * fo), budget, ask - 1)))
        assert len(c_on) == len(c_off) - 1 and r_on.get("harness_skipped") == 1, (len(c_on), len(c_off))


def test_card_cache():
    d = tempfile.mkdtemp()
    card = {"name": "s", "supportedInterfaces": [{"protocolBinding": "JSONRPC", "url": "https://s/a2a"}]}
    gets = []

    class Resp:
        def json(self):
            return card

    def get(self, url, timeout=None):
        gets.append((url, timeout))
        return Resp()
    with mock.patch.object(bs, "CACHE", os.path.join(d, "c.json")), mock.patch.object(httpx.Client, "get", get):
        a = bs.client("https://s", buyer_biz.A2AClient, {}, now=1000, r=ON)
        b = bs.client("https://s", buyer_biz.A2AClient, {}, now=1500, r=ON)
        c = bs.client("https://s", buyer_biz.A2AClient, {}, now=1700, r=ON)
    assert a.url == b.url == "https://s/a2a" and not a.cached and b.cached and not c.cached
    assert len(gets) == 2 and gets[0][1] == 10


def test_slow():
    def get(self, url, timeout=None):
        raise httpx.ReadTimeout("slow")
    d = tempfile.mkdtemp()
    with mock.patch.object(bs, "CACHE", os.path.join(d, "c.json")), mock.patch.object(httpx.Client, "get", get):
        try:
            bs.client("https://s", buyer_biz.A2AClient, {}, now=1000, r=ON)
            assert False
        except bs.SlowShop as e:
            assert e.stage == "card"
    fc = FakeClient(100)

    def slow_send(msg, tid=None):
        raise httpx.ReadTimeout("slow")
    fc.send = slow_send
    try:
        bs.first_send(fc, "hi", r=ON)
        assert False
    except bs.SlowShop as e:
        assert e.stage == "first reply" and fc.http.timeout == 120


def test_regulars():
    """11/09 ~17:20: shops with >= 20 deals with us in 24 h get 20 s for the card and the first reply; refreshed hourly."""
    import sqlite3
    d = tempfile.mkdtemp()
    db = os.path.join(d, "m.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE buyer_deals (ts REAL, shop TEXT, outcome TEXT)")
    now = 1_000_000
    con.executemany("INSERT INTO buyer_deals VALUES (?,?,?)",
                    [(now - 100, "reg", "deal")] * 20 + [(now - 100, "few", "deal")] * 19 + [(now - 90_000, "old", "deal")] * 30
                    + [(now - 100, "fails", "fail")] * 40)
    con.commit(); con.close()
    R = dict(ON, regular_reply_s=20, regular_min_deals=20)
    with mock.patch.object(bs, "REGULARS", os.path.join(d, "r.json")), mock.patch.object(bs, "rules", return_value=R):
        assert bs.regulars(now=now, db_path=db) == {"reg": 20}
        con = sqlite3.connect(db)
        con.executemany("INSERT INTO buyer_deals VALUES (?,?,?)", [(now, "few", "deal")])
        con.commit(); con.close()
        assert bs.regulars(now=now + 3599, db_path=db) == {"reg": 20}               # cached for an hour
        assert bs.regulars(now=now + 3601, db_path=db) == {"reg": 20, "few": 20}    # refreshed
        with mock.patch.object(bs, "regulars", lambda: {"reg": 20}):
            assert bs.limit("reg", R) == (20.0, True)
            assert bs.limit("other", R) == (10.0, False)
            assert bs.limit("reg", ON) == (10.0, False)                               # rule key missing: unchanged
    # the limit reaches the card fetch and the first send
    gets = []

    class Resp:
        def json(self):
            return {"name": "s", "supportedInterfaces": [{"protocolBinding": "JSONRPC", "url": "https://s/a2a"}]}

    def get(self, url, timeout=None):
        gets.append(timeout)
        return Resp()
    with mock.patch.object(bs, "CACHE", os.path.join(d, "c.json")), mock.patch.object(httpx.Client, "get", get):
        bs.client("https://s", buyer_biz.A2AClient, {}, now=1000, r=ON, lim=20.0)
    assert gets == [20.0]
    fc, seen = FakeClient(100), []
    real = fc.send

    def send(msg, tid=None):
        seen.append(fc.http.timeout)
        return real(msg, tid)
    fc.send = send
    bs.first_send(fc, "hi", r=ON, lim=20.0)
    assert seen[0].read == 20.0 and fc.http.timeout == 120


if __name__ == "__main__":
    test_same_first_offer()
    test_card_cache()
    test_slow()
    test_regulars()
    print("ok")
