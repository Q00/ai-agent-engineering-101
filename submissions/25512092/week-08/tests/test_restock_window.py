"""The real restock.py path through a fake supply window (person 11/11 ~15:25: a new feature goes live only with a
test through the operating path): restock.main() run 1 = the window's first plan (one plan of 2 x MAX_LINES, the
first MAX_LINES printed, the rest saved), run 2 = the saved second plan printed at once (no refresh / ticks /
planning), fitted to the close; no SKU in both. Everything that touches the market, the db or live files is faked.
    python tests/test_restock_window.py"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_path  # noqa: E402

policy()
import limits  # noqa: E402
import restock  # noqa: E402
from depts import purchasing as PU, supply_window as W  # noqa: E402


def fake_plan(inv, units, catalog, budget, log):
    """As make_plan: at most purchasing.MAX_LINES lines, one per SKU (reads the module value restock.py raises)."""
    return [{"sku": f"G{i:02d}", "qty": 1} for i in range(min(PU.MAX_LINES, 17))]


def run(d, restock_info, calls):
    with open(os.path.join(d, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump({"balance": 50_000_000, "units": [], "restock": restock_info}, f)
    with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    P = mock.patch.object
    noop = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (restock, "refresh", lambda **k: calls.append("refresh")),
                (restock, "record_stock", noop), (restock, "record_cash", noop), (restock, "log", noop),
                (restock.strategy, "tick", noop), (restock.strategy, "shop_case", lambda inv: "normal"),
                (restock.sales_report, "tick", noop), (restock.forum_post, "run", noop),
                (restock.dept_reports, "tick", noop), (restock.ceo_policy, "tick", noop),
                (restock.alerts, "watch", noop), (restock.pricing, "restock_stopped", lambda: False),
                (restock.pricing, "held_cost", lambda inv: 0), (restock, "cap_record", noop),
                (restock, "make_plan", lambda *a: calls.append("plan") or fake_plan(*a)),
                (restock, "spend_share", lambda: 0.4), (restock, "C_WINDOW", os.path.join(d, "c_window.json")),
                (restock.decisions, "record", noop),
                (limits, "buy_paused", lambda now=None: False),
                (limits, "stock_cap_now", lambda *a, **k: (10 ** 10, 10 ** 10, "auto")),
                (W, "SECOND", os.path.join(d, "plan2.json")),
                (W, "wait_open", lambda r, log=print: calls.append("wait"))):
            es.enter_context(P(obj, name, val))
        es.enter_context(mock.patch("depts.purchasing.c_rules", lambda: {"max_first_run": True, "first_run_share": 0.7}))
        es.enter_context(mock.patch("depts.forum_watch.tick", noop))
        es.enter_context(mock.patch("depts.alliance.tick", noop))
        es.enter_context(mock.patch("decisions.record", noop))
        es.enter_context(mock.patch.object(sys, "argv", ["restock.py", "--state", d]))
        with contextlib.redirect_stdout(out):
            restock.main()
    return json.loads(out.getvalue().strip().splitlines()[-1])["buy"]


def test_two_plans_in_one_window():
    d = tempfile.mkdtemp()
    now = time.time()
    win = {"open": False, "opens_at": now + 100, "closes_at": now + 280}        # opens in 100 s: plan early
    calls = []
    first = run(d, win, calls)
    assert len(first) == limits.MAX_LINES and calls == ["refresh", "plan", "wait"]
    assert PU.MAX_LINES == limits.MAX_LINES                                     # put back after the 2 x plan
    assert os.path.exists(os.path.join(d, "plan2.json"))
    calls = []
    second = run(d, dict(win, open=True), calls)                                # the kit's next run
    assert calls == [] and len(second) == 7                                     # at once: no refresh, no planning
    assert not ({p["sku"] for p in first} & {p["sku"] for p in second})         # no SKU twice
    assert not os.path.exists(os.path.join(d, "plan2.json"))
    calls = []
    third = run(d, dict(win, open=True), calls)                                 # nothing saved: a normal run
    assert "refresh" in calls and "plan" in calls and len(third) <= limits.MAX_LINES


def test_second_plan_fitted_to_close():
    d = tempfile.mkdtemp()
    now = time.time()
    run(d, {"open": False, "opens_at": now + 60, "closes_at": now + 240}, [])
    with open(os.path.join(d, "plan2.json"), encoding="utf-8") as f:
        s = json.load(f)
    s["opens_at"], s["closes_at"] = time.time() - 140, time.time() + 40          # only ~40 s left at the 2nd run
    with open(os.path.join(d, "plan2.json"), "w", encoding="utf-8") as f:
        json.dump(s, f)
    second = run(d, {"open": True, "opens_at": s["opens_at"], "closes_at": s["closes_at"]}, [])
    assert len(second) == 3                                                      # 3 + 2 x 12 <= 40 - 5


def test_failure_falls_back():
    """A broken second-plan path must not cost the run: the normal plan still goes out."""
    d = tempfile.mkdtemp()
    now = time.time()
    calls = []
    with mock.patch.object(W, "take_second", side_effect=AttributeError("x")):
        got = run(d, {"open": True, "opens_at": now - 10, "closes_at": now + 170}, calls)
    assert "plan" in calls and got


if __name__ == "__main__":
    test_two_plans_in_one_window()
    test_second_plan_fitted_to_close()
    test_failure_falls_back()
    print("ok")
