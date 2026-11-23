"""Plan D second_shop, high_accept_105 and the staged saved guard (person's decision 11/09 ~17:40), through the buyer's
main loop with the shops' negotiations faked.
    python tests/test_second_shop.py"""
import contextlib
import io
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depts import accept_rule, buyer_biz as bb, high_accept, saved_guard, second_shop as d2, soft_neg  # noqa: E402
from _isolate import policy, tmp_path  # noqa: E402

policy()                                          # a temp policy.json (code defaults)
from depts import shop_order  # noqa: E402
shop_order.RULES, shop_order.LOG = tmp_path("shop_order.json"), tmp_path("no_stock.jsonl")   # not the live files
from depts import buyer_speed  # noqa: E402
buyer_speed.RULES = tmp_path("buyer_speed.json")

D2_ON = {"enabled": True, "since": 0}
HA_ON = {"enabled": True, "since": 0, "min_list": 200_000, "factor": 1.05}
SN = {"enabled": True, "since": 0, "until": 9e12, "share": 1.0, "first": 0.75, "haggles": 2, "accept": 1.0, "low": 0.5}


def res(deal=False, ended="walk", price=None, **kw):
    r = {"deal": deal, "receipt": {"price": price, "item": "GX"} if deal else None, "agreed": price, "first_ask": None,
         "last_ask": None, "injection": False, "solicit": False, "last_state": "", "error": None, "offers": 2,
         "ended": "" if deal else ended, "harness_calls": 0, "soft_neg": 1}
    r.update(kw)
    return r


def run(results, d2r=D2_ON, har=None, lp=50_000, avg=40_000, budget=60_000, n_shops=3, arm="soft"):
    """results: one negotiate() result per shop tried, in order. Returns (negotiate calls, record_deal cases, soft log)."""
    d = tempfile.mkdtemp()
    shops = [{"name": f"s{i}", "url": f"https://s{i}", "offers": [{"sku": "GX", "qty": 1, "name": "x"}], "shop_score": 0.5}
             for i in range(n_shops)]
    with open(os.path.join(d, "shops.json"), "w", encoding="utf-8") as f:
        json.dump(shops, f)
    with open(os.path.join(d, "order.json"), "w", encoding="utf-8") as f:
        json.dump({"order": "o-1", "acceptable": ["GX"], "kind": "goods"}, f)
    calls, cases, slog = [], [], []
    it = iter(results)

    def neg(url, item, cap, start, *a, **k):
        calls.append({"url": url, "cap": cap, "soft": k.get("soft"), "high": k.get("high")})
        r = next(it)
        if isinstance(r, Exception):                  # a shop that did not answer
            raise r
        return dict(r)
    argv = ["x", "--shops", os.path.join(d, "shops.json"), "--item", "x", "--budget", str(budget),
            "--order", os.path.join(d, "order.json")]
    P = mock.patch.object
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (bb, "load_memory", lambda: {}), (bb, "load_stats", lambda: {}),
                (bb, "load_catalog", lambda: {"GX": {"list_price": lp, "kind": "goods"}}),
                (bb, "choose", lambda shops, *a: (shops, "test")), (bb, "min_offers_for", lambda *a: 1),
                (bb, "pick_sku", lambda offers, *a: (offers[0], avg, 10)), (bb, "negotiate", neg),
                (bb, "judge", lambda r, *a: (5, "ok", "ok") if r["deal"] else (2, "no", "walk")),
                (bb, "remember", lambda row: None), (bb, "refuse_receipt", lambda *a, **k: False),
                (bb, "record_deal", lambda c, sku, lp_, case, *a: cases.append((c["name"], case))),
                (bb, "log", lambda m: None),
                (bb.strategy, "buyer_case", lambda: "normal"), (bb.strategy, "pick", lambda k: ("0.6", {})),
                (soft_neg, "rules", lambda: SN), (soft_neg, "arm", lambda o, c, r: arm),
                (soft_neg, "log", lambda **kw: slog.append(kw)),
                (d2, "rules", lambda: d2r), (high_accept, "rules", lambda: har or {}),
                (sys, "argv", argv)):
            es.enter_context(P(obj, name, val))
        from depts import buyer_break, buyer_tactic
        es.enter_context(P(buyer_tactic, "pick", lambda: ("plain", {})))
        es.enter_context(P(buyer_break, "order_applies", lambda *a: False))
        rec = []
        es.enter_context(mock.patch("decisions.record", lambda *a, **k: rec.append(a)))
        with contextlib.redirect_stdout(io.StringIO()):
            bb.main()
    return calls, cases, slog, rec


def test_second_shop():
    # first shop walks -> the second shop, soft_neg way, closes: recorded as second_shop
    calls, cases, slog, _ = run([res(ended="walk"), res(deal=True, price=30_000)])
    assert [c["url"] for c in calls] == ["https://s0", "https://s1"]
    assert calls[1]["soft"] is not None and calls[1]["soft"]["first"] == 0.75
    assert cases == [("s0", "soft_neg"), ("s1", "second_shop")]
    assert slog[-1]["second_shop"] and slog[-1]["first_shop"]["shop"] == "s0" and slog[-1]["deal"]
    # the shop ended it (no stock is a shop-ended reply) -> also retried; at most one 2nd shop
    calls, cases, _, _ = run([res(ended="seller_ended"), res(ended="walk"), res(deal=True, price=1)])
    assert len(calls) == 2 and cases[-1] == ("s1", "second_shop")
    # D off, a control-arm order, one shop in stock, an error or a precheck skip: no D
    assert len(run([res(), res(deal=True, price=1)], d2r={})[0]) == 1
    assert len(run([res(), res(deal=True, price=1)], arm="control")[0]) == 1
    assert len(run([res(), res(deal=True, price=1)], n_shops=1)[0]) == 1
    # 11/09 ~20:00: a timeout is D's case too (within 30 s), reason 무응답
    calls, cases, slog, _ = run([res(ended="time_limit"), res(deal=True, price=1)])
    assert len(calls) == 2 and slog[-1]["first_shop"]["why"] == "무응답"
    # no time left within the 30 s limit
    assert len(run([res(), res(deal=True, price=1)], d2r=dict(D2_ON, limit_s=5))[0]) == 1
    # the first shop does not answer (slow shop): the next shop as D, soft_neg way, tagged second_shop
    from depts import buyer_speed
    slow = buyer_speed.SlowShop("first reply", 10.0)
    calls, cases, slog, _ = run([slow, res(deal=True, price=30_000)])
    assert len(calls) == 2 and calls[1]["soft"]["first"] == 0.75 and cases[-1] == ("s1", "second_shop")
    assert slog[-1]["first_shop"]["why"] == "무응답" and slog[-1]["first_shop"]["ended"] == "no_reply"
    # over the 30 s: the next shop is still tried (the slow-shop rule, as before D) but not counted as D
    calls, cases, slog, _ = run([slow, res(deal=True, price=30_000)], d2r=dict(D2_ON, limit_s=5))
    assert len(calls) == 2 and cases[-1][1] == "soft_neg" and slog[-1]["first_shop"] is None
    # control arm: the slow-shop rule only, no D
    calls, cases, slog, _ = run([slow, res(deal=True, price=30_000)], arm="control")
    assert len(calls) == 2 and slog[-1]["first_shop"] is None
    # reasons: no stock vs ended
    assert d2.reason(res(ended="seller_ended", end_text="Sorry, none of that in stock.")) == "재고"
    assert d2.reason(res(ended="seller_ended", end_text="Bye")) == "끊김" and d2.reason(res(ended="walk")) == "가격"


def test_by_reason():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import saved_guard_watch as gw
    D = [{"deal": True, "first_shop": {"why": "무응답"}}, {"deal": False, "first_shop": {"why": "무응답"}},
         {"deal": True, "first_shop": {"ended": "walk"}}, {"deal": False, "first_shop": {"why": "재고"}}]
    assert gw.by_reason(D) == "  첫 가게 종료 이유별 D 시도 / 성사율: 가격 1건 / 100%, 재고 1건 / 0%, 무응답 2건 / 50%"


def test_fits():
    assert d2.fits(17.0, {}) and not d2.fits(17.1, {})          # 17 + 9 + 1.5 + 2.5 = 30 s from the order start
    assert d2.qualifies(res(ended="turn_limit")) and not d2.qualifies(res(deal=True, price=1))
    assert not d2.qualifies(res(ended="precheck", skip="shelf")) and not d2.qualifies(res(injection=True))


def test_high_accept():
    # list 300,000, avg 250,000, budget 400,000: cap 262,500, soft accept bound 1.05, accept-rule factor 1.05
    calls, cases, slog, rec = run([res(deal=True, price=260_000)], har=HA_ON, lp=300_000, avg=250_000, budget=400_000)
    assert calls[0]["cap"] == 262_500 and calls[0]["high"] == 1.05 and calls[0]["soft"]["accept"] == 1.05
    assert calls[0]["soft"]["haggles"] == 2                       # no extra_haggles in the rules: unchanged
    # 11/09 ~21:20: x 1.10 and one more haggle (2 -> 3) for list >= 200,000
    calls = run([res()], har=dict(HA_ON, factor=1.10, extra_haggles=1), lp=300_000, avg=250_000, budget=400_000)[0]
    assert calls[0]["cap"] == 275_000 and calls[0]["soft"]["accept"] == 1.10 and calls[0]["soft"]["haggles"] == 3
    calls = run([res()], har=dict(HA_ON, factor=1.10, extra_haggles=1), lp=150_000, avg=120_000, budget=400_000)[0]
    assert calls[0]["soft"]["haggles"] == 2 and calls[0]["soft"]["accept"] == 1.0           # under 200,000: unchanged
    assert slog[-1]["high_accept"] and any(a[1] == high_accept.TAG for a in rec)
    # a deal at or under the average is not tagged
    _, _, slog, rec = run([res(deal=True, price=240_000)], har=HA_ON, lp=300_000, avg=250_000, budget=400_000)
    assert not slog[-1]["high_accept"] and not rec
    # under 200,000 list, or the rule off: cap at the average, no factor
    calls = run([res()], har=HA_ON, lp=150_000, avg=120_000, budget=400_000)[0]
    assert calls[0]["cap"] == 120_000 and calls[0]["high"] is None and calls[0]["soft"]["accept"] == 1.0
    calls = run([res()], har={}, lp=300_000, avg=250_000, budget=400_000)[0]
    assert calls[0]["cap"] == 250_000 and calls[0]["high"] is None
    # the budget still bounds the cap
    assert run([res()], har=HA_ON, lp=300_000, avg=250_000, budget=255_000)[0][0]["cap"] == 255_000


def test_soft_105():
    """11/11 (person): soft orders accept at once up to 1.05 x the market average, within the budget; list >= 200,000
    keeps 1.10 (high_accept); control orders keep the cap at the average."""
    global SN
    old = SN
    SN = dict(old, accept=1.05)
    try:
        calls = run([res()], lp=50_000, avg=40_000, budget=60_000)[0]
        assert calls[0]["cap"] == 42_000 and calls[0]["soft"]["accept"] == 1.05
        assert soft_neg.move(42_000, None, 0, calls[0]["cap"], 40_000, calls[0]["soft"])["action"] == "accept"
        assert soft_neg.move(42_001, None, 0, calls[0]["cap"], 40_000, calls[0]["soft"])["action"] == "offer"
        assert run([res()], lp=50_000, avg=40_000, budget=41_000)[0][0]["cap"] == 41_000          # budget bounds it
        calls = run([res()], har=dict(HA_ON, factor=1.10), lp=300_000, avg=250_000, budget=400_000)[0]
        assert calls[0]["cap"] == 275_000 and calls[0]["soft"]["accept"] == 1.10                # 200,000+: x 1.10
        calls = run([res()], lp=50_000, avg=40_000, budget=60_000, arm="control")[0]
        assert calls[0]["cap"] == 40_000 and calls[0]["soft"] is None                          # control: unchanged
    finally:
        SN = old


def test_accept_rule_factor():
    with mock.patch.object(accept_rule, "rules", lambda: {"enabled": True, "since": 0, "until": None, "low": 0.5, "high": 1.0}):
        assert accept_rule.price(104, 200, 100) is None
        assert accept_rule.price(104, 200, 100, factor=1.05) == 104
        assert accept_rule.price(106, 200, 100, factor=1.05) is None
        assert accept_rule.price(104, 103, 100, factor=1.05) is None          # over the cap


def test_soft_move_accept():
    assert soft_neg.move(104, None, 0, 105, 100, dict(SN, accept=1.05))["action"] == "accept"
    assert soft_neg.move(104, None, 0, 105, 100, SN)["action"] == "offer"


def test_saved_guard():
    # 11/12 ~18:30 (person, saved rescaled by agent.md patch 3.6): every fixed threshold removed - saved never switches
    # a feature now (the A-grade switch is in depts/saved_guard.a_switch, tests/test_grade_rule.py)
    b = lambda s, f4=None: {"saved": s, "second": 0.6, "third": 0.5, "fourth": f4, "saved_rank": 3}
    for f in ("high_accept", "second_shop", "soft_neg", "accept_rule"):
        for v in (0.1, 0.3829, 0.395, 0.8):
            assert saved_guard.why(f, b(v, 0.3773)) is None and saved_guard.back(f, b(v)) is None


def test_apply_and_watch():
    """20:27: on only when not already under the guard; the watcher switches off and on again by absolute saved."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import saved_guard_watch as gw
    import soft_neg_mid as mid
    gw.LOG = tmp_path("saved_guard.log")             # not logs/saved_guard.log
    d = tempfile.mkdtemp()
    rec, sent = [], []
    dec = mock.Mock(record=lambda *a, **k: rec.append(a))
    al = mock.Mock(send=lambda *a, **k: sent.append(a))
    J = lambda n, v: open(os.path.join(d, n), "w", encoding="utf-8").write(json.dumps(v))
    J("sn.json", {"enabled": True, "since": 1, "until": 2 * 10 ** 10, "share": 1.0})
    J("ar.json", {"enabled": True, "since": 1, "until": None, "mode": "always"})
    with mock.patch.object(d2, "RULES", os.path.join(d, "d2.json")),             mock.patch.object(high_accept, "RULES", os.path.join(d, "ha.json")),             mock.patch.object(soft_neg, "RULES", os.path.join(d, "sn.json")),             mock.patch.object(accept_rule, "RULES", os.path.join(d, "ar.json")):
        t = mid.apply_d_high({"saved": 0.42}, 1000, dec)
        assert d2.rules()["enabled"] and d2.rules()["applied_by"] == "soft_neg_mid" and high_accept.rules()["factor"] == high_accept.FACTOR
        assert "적용" in t and len(rec) == 2
        for v in (0.393, 0.382, 0.41, 0.8):                  # 11/12 ~18:30: saved switches nothing any more
            assert gw.check({"saved": v}, al, dec) == []
        assert all(m.on() for m in (high_accept, d2, soft_neg, accept_rule)) and not sent
        # at 20:27 already under the guard: high not switched on, D on
        os.remove(d2.RULES); os.remove(high_accept.RULES)
        t = mid.apply_d_high({"saved": 0.392}, 1000, dec)
        assert d2.rules()["enabled"] and high_accept.rules()["enabled"]   # 11/12 ~18:30: no saved threshold blocks it


def test_d_effect():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import saved_guard_watch as gw
    R = [{"order": "a", "arm": "soft", "deal": True, "order_secs": 18.0, "first_shop": {"secs": 6.5}},   # +11 s
         {"order": "b", "arm": "soft", "deal": False, "order_secs": 10.0, "first_shop": {"secs": 5.5}}]  # +4 s
    R += [{"order": f"o{i}", "arm": "soft", "deal": i < 3, "order_secs": 8.0} for i in range(10)]        # 30% close
    cyc = {"a": 30.0, "b": 21.0}
    cyc.update({f"o{i}": 21.0 for i in range(10)})
    L = gw.d_effect(R, cyc, 1.0)
    # a: 30 -> without D max(20.6, 19) = 20.6 (9.4 s); b: 21 -> max(20.6, 17) = 20.6 (0.4 s); 9.8 s / mean 21.75 s
    assert "+7.5초" in L[0] and "합 10초" in L[0]
    assert "D 성사 +1.0/h" in L[1] and "0.5/h x 첫 가게 성사율 30%" in L[1] and "= +0.9/h" in L[1], L[1]
    assert gw.d_effect([], {}, 1.0) == ["- D 시간·순증: 시도 없음"]


if __name__ == "__main__":
    test_d_effect()
    test_second_shop(); test_fits(); test_high_accept(); test_accept_rule_factor(); test_soft_move_accept()
    test_saved_guard(); test_apply_and_watch(); test_by_reason(); test_soft_105()
    print("ok")
