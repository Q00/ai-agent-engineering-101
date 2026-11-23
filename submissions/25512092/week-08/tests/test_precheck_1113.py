"""Before haggling (person 11/13 ~13:40): (a) the shelf life the shop states ("shelf_left=163h") under the order's
use_within_hours, (b) a stated grade ("grade=C") outside the accepted A / B (goods without a warranty) -> no
negotiation, the next shop. Through the buyer's main loop with the real negotiate() and fake shops (A2AClient).
    python tests/test_precheck_1113.py"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()
from depts import accept_rule, buyer_biz as B, buyer_speed, high_accept, second_shop as d2, soft_neg  # noqa: E402
from depts import shop_avoid as _SA, shop_order  # noqa: E402
_SA.EVENTS, _SA.STATE, _SA.MISSED, _SA.RULES = (tmp_path("ev.jsonl"), tmp_path("s.json"), tmp_path("m.jsonl"),
                                                tmp_path("r.json"))
shop_order.RULES, shop_order.LOG = tmp_path("shop_order.json"), tmp_path("no_stock.jsonl")
buyer_speed.RULES = tmp_json({"enabled": False}, "bs")
accept_rule.RULES = tmp_json({"enabled": True, "since": 0, "until": None, "low": 0.5, "high": 1.0}, "ar")
accept_rule.LOG = tmp_path("accept_rule.jsonl")
SN = {"enabled": True, "since": 0, "until": time.time() + 3600, "share": 1.0, "first": 0.65, "haggles": 2, "accept": 1.0}
soft_neg.RULES, soft_neg.LOG = tmp_json(SN, "sn"), tmp_path("soft_neg.jsonl")

DRAGON = ("[sku GX0000001] smoked duck | unit=u-1 grade={g} cert=market-signed shelf_left={h}h | list=45900 ask=9000 | "
          "asking 9000. Reply with a number.")
REPLIES = {}                                         # url -> first reply text
SENT = {}                                            # url -> texts sent


class Shop:
    def __init__(self, url):
        self.url, self.card = url, {"name": url, "skills": []}

    def send(self, text, task_id=None):
        SENT.setdefault(self.url, []).append(text)
        n = int(text) if text.strip().isdigit() else None
        if n is not None and n >= 9000:
            return {"id": "t", "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": [{"text": f"Deal at {n}."}]}},
                    "artifacts": [{"parts": [{"data": {"shop": self.url, "item": "GX0000001", "price": n, "task": "t"}}]}]}
        return {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                      "message": {"parts": [{"text": REPLIES[self.url]}]}}}

    def cancel(self, task_id):
        pass


def run(replies, kind, need=None):
    REPLIES.clear(), SENT.clear()
    d = tempfile.mkdtemp()
    shops = [{"name": f"s{i}", "url": f"https://s{i}", "offers": [{"sku": "GX0000001", "qty": 1, "name": "x"}], "shop_score": 0.5}
             for i in range(len(replies))]
    for s, r in zip(shops, replies):
        REPLIES[s["url"]] = r
    json.dump(shops, open(os.path.join(d, "shops.json"), "w", encoding="utf-8"))
    order = {"order": "o-1", "acceptable": ["GX0000001"], "kind": kind}
    if need:
        order["use_within_hours"] = need
    json.dump(order, open(os.path.join(d, "order.json"), "w", encoding="utf-8"))
    argv = ["x", "--shops", os.path.join(d, "shops.json"), "--item", "x", "--budget", "20000",
            "--order", os.path.join(d, "order.json")]
    logs, out = [], io.StringIO()
    P = mock.patch.object
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (B, "A2AClient", Shop), (B, "ask_harness", lambda p: (_ for _ in ()).throw(AssertionError("no LLM"))),
                (B, "load_memory", lambda: {}), (B, "load_stats", lambda: {}),
                (B, "load_catalog", lambda: {"GX0000001": {"list_price": 20000, "kind": kind}}),
                (B, "choose", lambda shops, *a: (shops, "test")), (B, "min_offers_for", lambda *a: 1),
                (B, "pick_sku", lambda offers, *a: (offers[0], 10000, 10)),
                (B, "remember", lambda row: None), (B, "record_deal", lambda *a, **k: None),
                (B, "grade_order", lambda o, *a: o), (B, "log", logs.append),
                (B.strategy, "buyer_case", lambda: "normal"), (B.strategy, "pick", lambda k: ("0.65", {})),
                (soft_neg, "arm", lambda o, c, r: "soft"), (soft_neg, "log", lambda **kw: None),
                (d2, "rules", lambda: {"enabled": False}), (high_accept, "rules", lambda: {}),
                (sys, "argv", argv)):
            es.enter_context(P(obj, name, val))
        from depts import buyer_break, buyer_tactic, grade_rule
        es.enter_context(P(buyer_tactic, "pick", lambda: ("plain", {})))
        es.enter_context(P(buyer_break, "order_applies", lambda *a: False))
        es.enter_context(P(grade_rule, "rules", lambda path=None: dict(grade_rule.DEFAULT, enabled=False)))
        es.enter_context(mock.patch("decisions.record", lambda *a, **k: None))
        es.enter_context(mock.patch("alerts.send", lambda *a, **k: None))
        with contextlib.redirect_stdout(out):
            B.main()
    return json.loads(out.getvalue().strip().splitlines()[-1]), logs


def offers(url):
    return [t for t in SENT.get(url, []) if t.strip().isdigit()]


def test_shelf_left():
    # (a) food, order needs 360 h: s0 states shelf_left=163h -> no offer there, s1 (400 h) closes
    out, logs = run([DRAGON.format(g="A", h=163), DRAGON.format(g="A", h=400)], "food", need=360)
    assert not offers("https://s0"), SENT
    assert any("shelf life 163.0 h < 360 h" in m for m in logs), logs
    assert any("s0 skipped" in m and "trying s1" in m for m in logs), logs
    assert out["receipt"] and out["shop"] == "https://s1", out
    # enough shelf life: no skip
    out, logs = run([DRAGON.format(g="A", h=400)], "food", need=360)
    assert out["receipt"] and not any("[precheck]" in m for m in logs), logs
    # goods with an order hour need and a stated shelf_left too (any kind; the kit's min_hours_left is for every order)
    out, logs = run([DRAGON.format(g="A", h=100), DRAGON.format(g="A", h=500)], "goods", need=360)
    assert not offers("https://s0") and out["shop"] == "https://s1", (SENT, out)


def test_grade():
    # (b) goods without a warranty: s0 states grade=C -> no offer there, s1 grade=B closes
    out, logs = run([DRAGON.format(g="C", h=999), DRAGON.format(g="B", h=999)], "goods")
    assert not offers("https://s0"), SENT
    assert any("grade C without a warranty (accepts A/B)" in m for m in logs), logs
    assert out["receipt"] and out["shop"] == "https://s1", out
    # grade C with a warranty: allowed
    out, logs = run([DRAGON.format(g="C", h=999).replace("asking", "48-hour breakage warranty. asking")], "goods")
    assert out["receipt"] and out["shop"] == "https://s0", (out, logs)
    # no grade stated: not skipped (nothing to judge before the deal)
    out, logs = run(["Item GX0000001. asking 9000."], "goods")
    assert out["receipt"], (out, logs)
    assert B.GRADE_RE.search("Grade: C")[1] == "C" and B.GRADE_RE.search("grade C")[1] == "C"
    assert B.GRADE_RE.search("need a shelf life, grade or warranty?") is None


if __name__ == "__main__":
    test_shelf_left()
    test_grade()
    print("ok")
