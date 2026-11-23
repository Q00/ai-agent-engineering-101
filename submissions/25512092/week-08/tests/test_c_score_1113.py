"""Grade C only at an expected new-saved score >= 0.75 (person 11/13 ~18:10): grade_rule.json C.min_score -> the cap at
the shop's first reply is list - 0.75 x (list - 0.35 x list) (with the break term without a warranty); no deal under it
-> the negotiation ends and the next shop is tried; A / B unchanged. Through the buyer's main loop with the real
negotiate() and fake shops (A2AClient).
    python tests/test_c_score_1113.py"""
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
from depts import grade_rule, shop_avoid as _SA, shop_order  # noqa: E402
_SA.EVENTS, _SA.STATE, _SA.MISSED, _SA.RULES = (tmp_path("ev.jsonl"), tmp_path("s.json"), tmp_path("m.jsonl"),
                                                tmp_path("r.json"))
shop_order.RULES, shop_order.LOG = tmp_path("shop_order.json"), tmp_path("no_stock.jsonl")
buyer_speed.RULES = tmp_json({"enabled": False}, "bs")
accept_rule.RULES = tmp_json({"enabled": True, "since": 0, "until": None, "low": 0.5, "high": 1.0}, "ar")
accept_rule.LOG = tmp_path("accept_rule.jsonl")
SN = {"enabled": True, "since": 0, "until": time.time() + 3600, "share": 1.0, "first": 0.65, "haggles": 2, "accept": 1.0}
soft_neg.RULES, soft_neg.LOG = tmp_json(SN, "sn"), tmp_path("soft_neg.jsonl")

LIST, AVG = 20000, 15000                 # C cap: 20000 - 0.75 x 13000 = 10250; A cap 1.05 x 15000 = 15750
GR = dict(grade_rule.DEFAULT, enabled=True, cost_share={"A": 0.65, "B": 0.5, "C": 0.35}, C={"first": 0.55, "min_score": 0.75})
TEXT = ("[sku GX0000001] smoked duck | unit=u-1 grade={g} cert=market-signed | list=20000 ask={a} | {w}asking {a}. "
        "Reply with a number.")
FLOOR = {}                                           # url -> lowest price the fake shop takes
SENT = {}


class Shop:
    def __init__(self, url):
        self.url, self.card = url, {"name": url, "skills": []}

    def send(self, text, task_id=None):
        SENT.setdefault(self.url, []).append(text)
        n = int(text) if text.strip().isdigit() else None
        g, a, w = FLOOR[self.url]
        if n is not None and n >= a:
            return {"id": "t", "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": [{"text": f"Deal at {n}."}]}},
                    "artifacts": [{"parts": [{"data": {"shop": self.url, "item": "GX0000001", "price": n, "task": "t"}}]}]}
        return {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                      "message": {"parts": [{"text": TEXT.format(g=g, a=a, w=w)}]}}}

    def cancel(self, task_id):
        pass


def run(shops_spec, gr=GR):
    """shops_spec: [(grade, price the shop holds at, warranty text)]."""
    FLOOR.clear(), SENT.clear()
    d = tempfile.mkdtemp()
    shops = [{"name": f"s{i}", "url": f"https://s{i}", "offers": [{"sku": "GX0000001", "qty": 1, "name": "x"}], "shop_score": 0.5}
             for i in range(len(shops_spec))]
    for s, spec in zip(shops, shops_spec):
        FLOOR[s["url"]] = spec
    json.dump(shops, open(os.path.join(d, "shops.json"), "w", encoding="utf-8"))
    json.dump({"order": "o-1", "acceptable": ["GX0000001"], "kind": "goods"}, open(os.path.join(d, "order.json"), "w",
                                                                                   encoding="utf-8"))
    argv = ["x", "--shops", os.path.join(d, "shops.json"), "--item", "x", "--budget", "20000",
            "--order", os.path.join(d, "order.json")]
    logs, out = [], io.StringIO()
    P = mock.patch.object
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (B, "A2AClient", Shop), (B, "ask_harness", lambda p: (_ for _ in ()).throw(AssertionError("no LLM"))),
                (B, "load_memory", lambda: {}), (B, "load_stats", lambda: {}),
                (B, "load_catalog", lambda: {"GX0000001": {"list_price": LIST, "kind": "goods"}}),
                (B, "choose", lambda shops, *a: (shops, "test")), (B, "min_offers_for", lambda *a: 1),
                (B, "pick_sku", lambda offers, *a: (offers[0], AVG, 10)),
                (B, "remember", lambda row: None), (B, "record_deal", lambda *a, **k: None),
                (B, "grade_order", lambda o, *a: o), (B, "log", logs.append),
                (B.strategy, "buyer_case", lambda: "normal"), (B.strategy, "pick", lambda k: ("0.65", {})),
                (soft_neg, "arm", lambda o, c, r: "soft"), (soft_neg, "log", lambda **kw: None),
                (d2, "rules", lambda: {"enabled": False}), (high_accept, "rules", lambda: {}),
                (grade_rule, "rules", lambda path=None: dict(gr)), (sys, "argv", argv)):
            es.enter_context(P(obj, name, val))
        from depts import buyer_break, buyer_tactic
        es.enter_context(P(buyer_tactic, "pick", lambda: ("plain", {})))
        es.enter_context(P(buyer_break, "order_applies", lambda *a: False))
        es.enter_context(mock.patch("decisions.record", lambda *a, **k: None))
        es.enter_context(mock.patch("alerts.send", lambda *a, **k: None))
        with contextlib.redirect_stdout(out):
            B.main()
    return json.loads(out.getvalue().strip().splitlines()[-1]), logs


def offers(url):
    return [int(t) for t in SENT.get(url, []) if t.strip().isdigit()]


W = "48-hour breakage warranty. "


def test_c_cap():
    # s0: C (with a warranty, so the kit pays) holding at 12,000 > the C cap 10,250 -> no deal, next shop s1 (A) closes
    out, logs = run([("C", 12000, W), ("A", 9000, "")])
    assert any("[grade C] cap" in m and "-> 10250" in m for m in logs), logs
    assert max(offers("https://s0")) <= 10250, SENT
    assert any("s0 skipped (grade C: no deal at or under 10250" in m and "trying s1" in m for m in logs), logs
    assert out["receipt"] and out["shop"] == "https://s1", out
    # s0: C holding at 10,000 (<= 10,250): the deal there
    out, logs = run([("C", 10000, W), ("A", 9000, "")])
    assert out["receipt"] and out["shop"] == "https://s0" and out["receipt"]["price"] <= 10250, (out, logs)


def test_a_b_unchanged():
    # A / B at 12,000: no C cap, the deal at s0 as before
    for g in ("A", "B"):
        out, logs = run([(g, 12000, ""), ("A", 9000, "")])
        assert out["shop"] == "https://s0" and out["receipt"]["price"] == 12000, (g, out, logs)
        assert not any("[grade C]" in m for m in logs), logs
    # without C.min_score: C at 12,000 closes at s0 (as before)
    out, logs = run([("C", 12000, W), ("A", 9000, "")], gr=dict(GR, C={"first": 0.55}))
    assert out["shop"] == "https://s0" and out["receipt"]["price"] == 12000, (out, logs)


if __name__ == "__main__":
    test_c_cap()
    test_a_b_unchanged()
    print("ok")
