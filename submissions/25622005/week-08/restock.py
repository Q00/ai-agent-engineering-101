"""The kit's restock hook (every 60 s in Round 3): ingest memory, plan the buy, decide bankruptcy.

Supply opens about every 20 minutes for 3 minutes; while it is closed and not opening within
stock.window_lead_s, the hook still ingests but buys nothing. While it is open or about to open, the hook
skips ingest and buys first: the closed-window runs between windows keep the summary fresh.

    python restock.py --state .market

Prints exactly one JSON line, {"buy": [{"sku", "qty"}], "bankrupt": true?}, and {"buy": []} on any error,
within 30 s. Diagnostics go to logs/hooks/restock.log; decisions to logs/decisions/restock.jsonl.

Patch 3.2 counts the whole market's units of a SKU in the rolling hour, ours included, and ingest pauses while
a window is open; so the lines this hook planned in the last hour are kept in memory/restock_planned.json, and
those the summary's ledger fold cannot have seen yet (planned after its build time minus PLANNED_LAG_S) are
added to the fold's count in the next plans.
"""
import argparse
import contextlib
import io
import json
import signal
import sys
import time
from pathlib import Path

HARD_LIMIT_S = 28                    # the line must be out before 30 s whatever hangs
INGEST_S = 20
PLANNED_S = 3600                     # the market's surge window
PLANNED_LAG_S = 600                  # a line planned this long before an ingest is in its ledger fold


class _Late(BaseException):
    """Not an Exception: the `except Exception` guards around ingest must not swallow the deadline."""


def _alarm(signum, frame):
    raise _Late(f"restock ran past {HARD_LIMIT_S} s")


def _planned_rows(path, now):
    """[[ts, sku, qty]] of our lines planned in the last PLANNED_S seconds; bad rows dropped."""
    from memory.store import read_json
    data = read_json(path, {})
    rows = data.get("lines") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, list) and len(r) == 3 and isinstance(r[0], (int, float))
            and now - PLANNED_S < r[0] <= now + 60 and isinstance(r[1], str) and isinstance(r[2], int)
            and not isinstance(r[2], bool)] if isinstance(rows, list) else []


def planned_units(path, now, since=None):
    """{sku: units} our lines planned in the last hour, only those planned after `since` when given."""
    out = {}
    for ts, sku, qty in _planned_rows(path, now):
        if since is None or ts > since:
            out[sku] = out.get(sku, 0) + qty
    return out


def record_planned(path, buy, now):
    """Add this run's lines to the rolling hour; a failed write is logged, never raised."""
    from memory.store import atomic_write_json, hook_log
    try:
        atomic_write_json(path, {"lines": _planned_rows(path, now) + [[now, b["sku"], b["qty"]] for b in buy]})
    except OSError as e:
        hook_log("restock", f"planned lines not saved: {type(e).__name__}: {e}")


def run(state, t0):
    import policy
    from memory import demand as demand_model
    from memory import ingest
    from memory.decisions import decision
    from memory.query import load_summary, summary_ref, supply_1h
    from memory.store import MEM, atomic_write_json, hook_log, read_json
    from tools import stock

    pol, ver, errors = policy.load()
    if errors:
        decision("restock", "policy_error", policy_ver=ver, inputs={"errors": errors[:10]},
                 choice="fallback", why="strategy/SKILL.md failed validation; using the last good policy or defaults")
    inv0 = read_json(state / "inventory.json", {})
    win0 = stock.supply_window(inv0 if isinstance(inv0, dict) else {}, time.time())
    if win0["open"] is True or (win0["open"] is False and win0["in_s"] is not None
                                and win0["in_s"] <= pol["stock"]["window_lead_s"]):
        ing = {"skipped": "window"}                          # an unknown window (in_s None) still ingests
    else:
        try:
            ing = ingest.sync(t0 + INGEST_S, state=state, window_h=policy.get(pol, "stock.demand_window_h"))
        except Exception as e:                               # memory is optional for this run
            hook_log("restock", f"ingest failed: {type(e).__name__}: {e}")
            ing = {"error": f"{type(e).__name__}: {e}"}

    inv = read_json(state / "inventory.json", {}) or {}
    catalog = ingest.load_catalog(state)
    demand = ingest.load_demand(state)
    summary = load_summary()
    now = time.time()
    out = {"buy": []}
    instock = None
    try:                                                     # the shelf history behind the demand model
        instock = demand_model.tick_instock(read_json(MEM / "instock.json", None), inv, now)
        atomic_write_json(MEM / "instock.json", instock)
    except Exception as e:
        hook_log("restock", f"instock tick error {type(e).__name__}: {e}")
    try:                                                     # D11: a report for a forum slot no deal post took
        from tools import forum
        forum.maybe_report(state, summary, catalog, pol, now, policy_ver=ver)
    except Exception as e:
        hook_log("restock", f"forum report error {type(e).__name__}: {e}")

    bankrupt, b_in = stock.should_bankrupt(inv, summary, pol, now)
    if isinstance(inv.get("balance"), (int, float)) and inv["balance"] < (inv.get("bankrupt_line") or 0):
        decision("restock", "bankrupt", policy_ver=ver, inputs=b_in, rules=["D10"], choice=bankrupt,
                 why="all D10 conditions hold" if bankrupt else "below the line but a D10 condition fails")
    if bankrupt:
        out["bankrupt"] = True
        return out

    url = ingest.own_url()
    card_ok = not policy.get(pol, "stock.require_own_card") or stock.own_card_ok(url)
    planned_path = MEM / "restock_planned.json"
    # our lines the summary's supply fold has not seen; without a fold, all of the last hour
    built = summary.get("built_at")
    since = built - PLANNED_LAG_S if supply_1h(summary, "") is not None and stock._finite(built) else None
    analyst = demand_model.load_forecast(read_json(MEM / "demand_forecast.json", None), now, pol)
    plan = stock.plan_restock(inv, catalog, demand, summary, pol, now,
                              planned=planned_units(planned_path, now, since), instock=instock, analyst=analyst)
    win = stock.supply_window(inv, now)
    held = dict(stock.held(inv), empty_picks=plan["empty_picks"], overpriced=plan["overpriced"])
    if not card_ok:
        decision("restock", "restock", policy_ver=ver,
                 inputs={"card_ok": False, "url": url, "ingest": ing, "window": win, "stock": held},
                 rules=["stock.require_own_card"], choice=[], why="our agent card does not answer; buy nothing")
        return out
    if stock.window_closed(win, pol):
        decision("restock", "restock", policy_ver=ver,
                 inputs={"balance": inv.get("balance"), "ingest": ing, "card_ok": card_ok, "window": win,
                         "stock": held},
                 rules=["stock.window_lead_s"], choice=[], why=f"supply window closed; opens in {win['in_s']} s",
                 elapsed_ms=int((time.time() - t0) * 1000))
        return out

    fit = stock.lines_that_fit(win)
    if fit is not None and fit < len(plan["buy"]):          # lines after the close would only wait and be skipped
        plan["excluded"].update({b["sku"]: "window_closing" for b in plan["buy"][fit:]})
        plan["buy"] = plan["buy"][:fit]
    out["buy"] = plan["buy"]
    reasons = {}
    for r in plan["excluded"].values():
        reasons[r] = reasons.get(r, 0) + 1
    planned = {b["sku"] for b in plan["buy"]}
    lists = {}
    for name in ("focus", "trend"):
        skus = policy.get(pol, f"stock.{name}_skus")
        lists[name] = {"skus": len(skus), "planned": len(planned & set(skus)),
                       "excluded": dict([(s, plan["excluded"].get(s, "not_planned")) for s in skus
                                         if s not in planned][:8])}
    listed = set(policy.get(pol, "stock.focus_skus")) | set(policy.get(pol, "stock.trend_skus"))
    # Patch 3.2 inputs of the planned and focus/trend SKUs the market bought this hour
    surge = {s: v for s, v in plan["surge"].items() if v["u1h"] > 0 and (s in planned or s in listed)}
    leaf = {sku: str((catalog.get(sku) or {}).get("category") or "").split(">")[-1] for sku in
            [b["sku"] for b in plan["buy"]]}
    refs = [summary_ref("cat", leaf[b["sku"]]) for b in plan["buy"]] + \
           [summary_ref("sku", b["sku"]) for b in plan["buy"]]
    decision("restock", "restock", policy_ver=ver,
             inputs={"balance": inv.get("balance"), "units": len(inv.get("units") or []),
                     "headroom": plan["headroom"], "excluded": reasons, "ingest": ing, "card_ok": card_ok,
                     "summary_built_at": summary.get("built_at"), "window": win, "stock": held,
                     "focus": lists["focus"], "trend": lists["trend"], "surge": surge,
                     "big_ticket": plan["big_ticket"],
                     "demand": {s: v for s, v in plan.get("demand", {}).items() if s in planned}},
             memory_refs=refs, rules=plan["rules"], choice=plan["buy"],
             why=f"{len(plan['buy'])} lines, worst cost {plan['worst_cost']} of headroom {plan['headroom']}",
             elapsed_ms=int((time.time() - t0) * 1000))
    if plan["buy"]:
        record_planned(planned_path, plan["buy"], now)
    return out


def main(argv=None):
    t0 = time.time()
    out = {"buy": []}
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(HARD_LIMIT_S)
        ap = argparse.ArgumentParser(description="Round 3 restock hook")
        ap.add_argument("--state", default=".market", help="the kit's .market folder")
        with contextlib.redirect_stderr(io.StringIO()):     # a usage error must not reach the kit log
            a = ap.parse_args(argv)
        out = run(Path(a.state), t0)
    except BaseException as e:                               # SystemExit from argparse included
        try:
            from memory.store import hook_log
            hook_log("restock", f"failed, buying nothing: {type(e).__name__}: {e}")
        except BaseException:
            pass
        out = {"buy": []}
    finally:
        signal.alarm(0)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
