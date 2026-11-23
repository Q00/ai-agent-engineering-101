"""Restock hook — the kit runs it every 60 s in Round 3:  python restock.py --state .market

1. Refreshes market.db / market_stats.json (at most once per 5 minutes, market_db.REFRESH_S).
2. Records the units my shop holds in market.db (my_stock: cost and the market average when they arrived).
3. Cash snapshot (market_db.record_cash), hourly/daily sales reports (sales_report.py), forum posts (forum_post.py).
4. Buys more of what sells fast across the market, only from cash above CUSHION, only while stock at cost is
   under MAX_STOCK_COST, never in the last 36 h of the round. Low-price SKUs (unit cost <= 100k) as before;
   high-price SKUs (100k-500k) in busy categories, one unit each, within the policy's share and unit cap.
Last line: {"buy": [{"sku": ..., "qty": ...}, ...]} (the kit buys at most 10 lines per run).
Departments (depts/): the plan is depts/purchasing.py; reports dept_reports.py; policy policy.json.
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
C_WINDOW = os.path.join(HERE, "logs", "restock_c_window.json")   # the window whose first plan is made
sys.path.insert(0, HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402
import dept_reports  # noqa: E402
from depts import ceo_policy  # noqa: E402
import policy  # noqa: E402
import forum_post  # noqa: E402
import pricing  # noqa: E402
import strategy  # noqa: E402
import sales_report  # noqa: E402
from depts.purchasing import (CUSHION, MAX_STOCK_COST, SPEND_SHARE, busy_categories, cap_record,  # noqa: E402,F401
                              make_plan, my_sold_30m, spend_share)
from market_db import ME, connect, load_stats, price_of, record_cash, record_stock, refresh  # noqa: E402
from sales_report import HIGH_MAX, HIGH_MIN  # noqa: E402

# the plan itself (purchasing department) is depts/purchasing.py; this file is the minute scheduler


def log(msg):
    print(f"[restock] {msg}", flush=True)
    if " failed" in msg and not msg.startswith("market_db refresh"):     # refresh blips: qc's refresh-age check
        try:
            import alerts
            alerts.send("urgent", "restock:" + msg.split(" failed")[0][:40], msg[:300])
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    state = ap.parse_args().state
    try:                                             # 11/11 ~18:25 (person): 11 s a line, back to 12 s at > 3 lines
        from depts import supply_window              # refused as closed in 3 h (before the 2nd plan is fitted)
        supply_window.guard(log=log)
    except Exception as e:
        log(f"[window] line guard error: {type(e).__name__}: {e}")
    try:                                             # 11/11 ~15:20 (person): the window's 2nd plan, made with the
        from depts import supply_window              # 1st, goes out at once (no refresh, ticks or planning first)
        plan2 = supply_window.take_second(state, log=log)
        if plan2 is not None:
            print(json.dumps({"buy": plan2}))
            return
    except Exception as e:
        log(f"[window] second plan failed, normal run: {type(e).__name__}: {e}")
    try:
        refresh(log=log)
    except Exception as e:
        log(f"market_db refresh failed: {e}")
    try:
        with open(os.path.join(state, "inventory.json"), encoding="utf-8") as f:
            inv = json.load(f)
        with open(os.path.join(state, "catalog.json"), encoding="utf-8") as f:
            catalog = json.load(f)
    except (OSError, ValueError) as e:
        log(f"no inventory/catalog yet: {e}")
        print(json.dumps({"buy": []}))
        return
    units = inv.get("units", [])
    try:
        record_stock(units, log=log)
    except Exception as e:
        log(f"record_stock failed: {e}")

    try:
        record_cash(inv, log=log)
    except Exception as e:
        log(f"record_cash failed: {e}")
    try:
        strategy.tick(inv, log=log)
    except Exception as e:
        log(f"strategy tick failed: {e}")
    try:
        sales_report.tick(inv, catalog, log=log)
    except Exception as e:
        log(f"sales report failed: {e}")
    try:
        forum_post.run(state, inv, catalog, log=log)
    except Exception as e:
        log(f"forum post failed: {e}")
    try:
        dept_reports.tick(inv, catalog, log=log)        # reports/dept/*.json every 5 min (read-only)
    except Exception as e:
        log(f"dept reports failed: {e}")
    try:
        ceo_policy.tick(inv, catalog, log=log)          # CEO: policy proposals (shadow) or policy.json (live)
    except Exception as e:
        log(f"ceo failed: {e}")
    try:                                                # forum archive + 15-min situation table (11/07)
        from depts import forum_watch
        forum_watch.tick(alerts.send, log)
    except Exception as e:
        log(f"forum watch failed: {type(e).__name__}: {e}")
    try:                                                # lee3q alliance: likes only, hourly cap (11/07 ~09:20)
        from depts import alliance
        alliance.tick(state, log)
    except Exception as e:
        log(f"alliance failed: {type(e).__name__}: {e}")
    try:
        alerts.watch(dept_reports.load(), log=log)     # urgent issues, kit restarts -> file, console, Telegram
    except Exception as e:
        print(f"[restock] alerts watch: {type(e).__name__}", flush=True)

    case = strategy.shop_case(inv)
    r = inv.get("restock") or {}
    now = time.time()
    from depts import supply_window                  # 11/11 ~14:50 (person): plan up to 150 s early, fit the close
    soon = supply_window.soon(r, now)
    if case in ("cash_low", "overstock") or pricing.restock_stopped():
        if (inv.get("restock") or {}).get("open"):
            log(f"case {case}{', last 36 h' if pricing.restock_stopped() else ''}: no restock")
            decisions.record("purchasing", "no_restock", case, 0, f"shop case {case}"
                             + (", last 36 h" if pricing.restock_stopped() else ""), limit_hit={"overstock": "STOCK_CAP", "cash_low": "CASH_RESERVE"}.get(case, "RESTOCK_STOP_H"))
        if case == "overstock" and not pricing.restock_stopped() and soon:
            safe(cap_record, inv, units, catalog, 0, [], case)    # log only: what the cap kept from buying
        print(json.dumps({"buy": []}))
        return
    if not soon:
        print(json.dumps({"buy": []}))           # window not open soon: nothing to plan yet
        return
    if supply_window.n_fit(r, now + 20) == 0:      # not even one line would reach the market before the close
        log("[window] too close to the close for one line: no plan (next window)")
        print(json.dumps({"buy": []}))
        return

    held_cost = pricing.held_cost(inv)
    import limits                                    # 11/09 ~23:10: the automatic cap, read every run
    cap_now = limits.stock_cap_now(inv.get("balance", 0), held_cost, now)[0]
    if limits.buy_paused(now=now):                   # the cash brake: no buying until cash > the resume level
        log(f"cash brake: buying paused until cash > {limits.auto_cap_rules().get('resume_cash', 0):,} "
            f"(cash {inv.get('balance', 0):,})")
        decisions.record("purchasing", "no_restock", case, 0, f"cash brake pause, cash {inv.get('balance', 0):,}",
                         limit_hit="STOCK_CAP")
        print(json.dumps({"buy": []}))
        return
    budget = min((inv.get("balance", 0) - CUSHION) * spend_share(), cap_now - held_cost)
    first_run = False
    try:                                             # bundle 2 (15): the first plan of a window spends all cash
        from depts.purchasing import c_rules         # above the reserve (restock_c.json, from 11/07 10:00)
        C, wpath = c_rules(), C_WINDOW
        win = r.get("opens_at")
        if C.get("max_first_run") and win:
            last = json.load(open(wpath)).get("opens_at") if os.path.exists(wpath) else None
            if last != win:
                share = float(C.get("first_run_share", 0.7))   # 11/07: at most 70% of the cash above the reserve
                full = min((inv.get("balance", 0) - CUSHION) * share, cap_now - held_cost)
                log(f"[window] first plan of the window opening {time.strftime('%H:%M', time.localtime(win))}: "
                    f"budget {int(budget):,} -> {int(full):,} ({share:.0%} of the cash above the reserve, within STOCK_CAP)")
                budget = max(budget, full)
                first_run = True
                json.dump({"opens_at": win, "ts": now, "budget": int(budget)}, open(wpath, "w"))
    except Exception as e:
        log(f"[window] full first run failed, normal budget: {type(e).__name__}: {e}")
    if budget <= 0:
        log(f"cash {inv.get('balance', 0):,} leaves nothing above the {CUSHION:,} reserve: no restock")
        decisions.record("purchasing", "no_restock", case, 0, f"budget {int(budget):,}: cash "
                         f"{inv.get('balance', 0):,}, stock at cost {held_cost:,}", limit_hit="CASH_RESERVE/STOCK_CAP")
        safe(cap_record, inv, units, catalog, 0, [], case)
        print(json.dumps({"buy": []}))
        return
    lines = []

    def plan_log(m):
        log(m)
        lines.append(m)
    if first_run and r.get("closes_at"):             # 11/11 ~15:20 (person): one plan of up to 2 x MAX_LINES, so the
        from depts import purchasing as PU           # 2nd plan knows the 1st (no SKU twice, one budget, one high room)
        import limits as LM
        PU.MAX_LINES = 2 * LM.MAX_LINES
        try:
            plan = make_plan(inv, units, catalog, budget, plan_log)
        finally:
            PU.MAX_LINES = LM.MAX_LINES
        plan, plan2 = plan[:LM.MAX_LINES], plan[LM.MAX_LINES:]
        supply_window.save_second(plan2, r, log=log)
    else:
        plan = make_plan(inv, units, catalog, budget, plan_log)
    plan, dropped = supply_window.fit(plan, r, log=log)
    if dropped:
        decisions.record("purchasing", "window_fit", "supply window", len(dropped),
                         f"{len(dropped)} lines would arrive after the close: dropped to the next window "
                         + ", ".join(f"{p['sku']} x{p['qty']}" for p in dropped))
    for m in lines:                                  # one decision per planned / skipped / merged line
        kind = ("buy_guard_skip" if m.startswith("[guard]") else "sofa_trial" if m.startswith("[sofa trial]")
                else "dedup" if m.startswith("[dedup]")
                else "skip" if m.endswith("-> skip") else "buy" if " -> buy " in m else None)
        if kind:
            decisions.record("purchasing", kind, m.split(":")[0][:60], m.rsplit("-> ", 1)[-1], m[:300])
    safe(cap_record, inv, units, catalog, budget, plan, case)
    if plan:
        supply_window.wait_open(r, log=log)      # planned early: the first line goes in at the opening
    print(json.dumps({"buy": plan}))


def safe(fn, *a):
    """Logging helpers must never cost the run its buy line."""
    try:
        fn(*a)
    except Exception as e:
        log(f"{fn.__name__} failed: {e}")


if __name__ == "__main__":
    main()
