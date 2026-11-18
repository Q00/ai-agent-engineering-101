"""At least 4 normal-goods lines a run (person 11/11 ~20:50), through purchasing.make_plan with the line ranking on (as
live): high lines score higher, yet each run-sized chunk (both halves of a window's 2 x MAX_LINES plan) has >= 4 normal
lines (est. cost <= 100,000), first; off without normal_min.json or with > 70 normal SKUs held.
    python tests/test_normal_min.py"""
import contextlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import market_db, policy, tmp_json  # noqa: E402

policy()
from depts import deep_high, line_rank, normal_min, purchasing as PU, top_high  # noqa: E402

CAT = "가전>TV"
RULES = {"enabled": True, "min_lines": 4, "max_cost": 100_000, "release_skus": 70}


def catalog():
    c = {f"H{i:02d}": {"name": f"high {i}", "list_price": 1_000_000, "kind": "goods", "category": CAT} for i in range(25)}
    c.update({f"L{i:02d}": {"name": f"low {i}", "list_price": 50_000, "kind": "goods", "category": CAT} for i in range(12)})
    return c


def plan_with(units, rules=RULES, lines=20):
    cat = catalog()
    sk = {k: {"name": k, "avg": int(v["list_price"] * 0.6 * 1.3), "n": 50, "sold_5m": 2, "sold_30m": 5}
          for k, v in cat.items()}
    st = {"skus": sk, "hot": list(sk), "wanted": [], "wanted_cats": [], "sku_break": {}, "sku_n": {}}
    P = mock.patch.object
    noop = lambda *a, **k: None
    with contextlib.ExitStack() as es:
        es.enter_context(market_db())                # 11/13 ~13:40: a temp market.db, never the live one
        for obj, name, val in (
                (normal_min, "RULES", tmp_json(rules, "normal_min")), (top_high, "RULES", tmp_json({}, "top_high")),
                (PU, "MAX_LINES", lines), (PU, "load_stats", lambda: st),
                (PU, "price_of", lambda k, s=None: (st["skus"][k]["avg"], 50)),
                (PU, "busy_categories", lambda c, top=5: ["TV"]), (PU, "c_rules", lambda: {}),
                (PU, "excluded", lambda: {}), (PU, "guard_check", lambda *a: None), (PU, "sofa_trial", lambda *a: None),
                (PU, "note_band", noop), (PU.sales_report, "policy", lambda: {"high_share": 0.7, "high_max": 100}),
                (deep_high, "current", lambda **k: {}), (deep_high, "limit", lambda sku, deep: 1),
                (PU.decisions, "record", noop), (PU.strategy, "current", lambda: ("normal", None)),
                (line_rank, "on", lambda *a: True), (line_rank, "market_24h", lambda: {})):
            es.enter_context(P(obj, name, val))
        es.enter_context(mock.patch("depts.overcost.mark", lambda units, f, log=None: set()))
        es.enter_context(mock.patch("depts.request_buy.candidates", lambda *a, **k: []))
        es.enter_context(mock.patch("depts.hot_fast.shares", lambda hot, share, log=None: {}))
        es.enter_context(mock.patch("depts.high_cost_exclude.current", lambda **k: {}))
        es.enter_context(mock.patch("depts.high_ask.buy_ok", lambda *a: (True, 0, 0)))
        logs = []
        plan = PU.make_plan({"balance": 10 ** 9, "units": units}, units, cat, 10 ** 9, logs.append)
    return plan, logs


low = lambda p: p["sku"].startswith("L")


def test_window_plan_both_halves():
    plan, logs = plan_with([])
    assert len(plan) == 20
    for half in (plan[:10], plan[10:]):
        assert sum(map(low, half)) >= 4 and all(low(p) for p in half[:4]), [p["sku"] for p in half]
    assert any("normal lines per run: 4, 4" in m or "normal lines per run" in m for m in logs)


def test_one_run():
    plan, _ = plan_with([], lines=10)
    assert sum(map(low, plan)) >= 4 and all(low(p) for p in plan[:4])


def test_off_and_release():
    plan, _ = plan_with([], rules={})                         # rule off: high lines win on score
    before = sum(map(low, plan[:10]))
    assert before < 4, before
    units = [{"unit": f"u{i}", "sku": f"N{i}", "cost": 50_000, "grade": "A"} for i in range(71)]
    plan, logs = plan_with(units)
    assert sum(map(low, plan[:10])) < 4 and any("off: 71 normal SKUs" in m for m in logs)
    units = units[:70]
    plan, _ = plan_with(units)
    assert sum(map(low, plan[:10])) >= 4                     # 70 held: still on


def test_arrange():
    P = [{"sku": s} for s in ["H1", "H2", "H3", "L1", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "L2", "L3", "L4", "L5"]]
    out = normal_min.arrange(P, lambda k: k.startswith("L"), 10, 4)
    assert [p["sku"] for p in out[:4]] == ["L1", "L2", "L3", "L4"] and len(out) == 15
    assert {p["sku"] for p in out} == {p["sku"] for p in P}


if __name__ == "__main__":
    test_window_plan_both_halves()
    test_one_run()
    test_off_and_release()
    test_arrange()
    print("ok")
