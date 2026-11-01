"""The kit's restock hook (every 60 s in Round 3): ingest memory, plan the buy, decide bankruptcy.

    python restock.py --state .market

Prints exactly one JSON line, {"buy": [{"sku", "qty"}], "bankrupt": true?}, and {"buy": []} on any error,
within 30 s. Diagnostics go to logs/hooks/restock.log; decisions to logs/decisions/restock.jsonl.
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


class _Late(BaseException):
    """Not an Exception: the `except Exception` guards around ingest must not swallow the deadline."""


def _alarm(signum, frame):
    raise _Late(f"restock ran past {HARD_LIMIT_S} s")


def run(state, t0):
    import policy
    from memory import ingest
    from memory.decisions import decision
    from memory.query import load_summary, summary_ref
    from memory.store import hook_log, read_json
    from tools import stock

    pol, ver, errors = policy.load()
    if errors:
        decision("restock", "policy_error", policy_ver=ver, inputs={"errors": errors[:10]},
                 choice="fallback", why="strategy/SKILL.md failed validation; using the last good policy or defaults")
    try:
        ing = ingest.sync(t0 + INGEST_S, state=state, window_h=policy.get(pol, "stock.demand_window_h"))
    except Exception as e:                                   # memory is optional for this run
        hook_log("restock", f"ingest failed: {type(e).__name__}: {e}")
        ing = {"error": f"{type(e).__name__}: {e}"}

    inv = read_json(state / "inventory.json", {}) or {}
    catalog = ingest.load_catalog(state)
    demand = ingest.load_demand(state)
    summary = load_summary()
    now = time.time()
    out = {"buy": []}

    bankrupt, b_in = stock.should_bankrupt(inv, summary, pol, now)
    if isinstance(inv.get("balance"), (int, float)) and inv["balance"] < (inv.get("bankrupt_line") or 0):
        decision("restock", "bankrupt", policy_ver=ver, inputs=b_in, rules=["D10"], choice=bankrupt,
                 why="all D10 conditions hold" if bankrupt else "below the line but a D10 condition fails")
    if bankrupt:
        out["bankrupt"] = True
        return out

    url = ingest.own_url()
    card_ok = not policy.get(pol, "stock.require_own_card") or stock.own_card_ok(url)
    if not card_ok:
        decision("restock", "restock", policy_ver=ver, inputs={"card_ok": False, "url": url, "ingest": ing},
                 rules=["stock.require_own_card"], choice=[], why="our agent card does not answer; buy nothing")
        return out

    plan = stock.plan_restock(inv, catalog, demand, summary, pol, now)
    out["buy"] = plan["buy"]
    reasons = {}
    for r in plan["excluded"].values():
        reasons[r] = reasons.get(r, 0) + 1
    leaf = {sku: str((catalog.get(sku) or {}).get("category") or "").split(">")[-1] for sku in
            [b["sku"] for b in plan["buy"]]}
    refs = [summary_ref("cat", leaf[b["sku"]]) for b in plan["buy"]] + \
           [summary_ref("sku", b["sku"]) for b in plan["buy"]]
    decision("restock", "restock", policy_ver=ver,
             inputs={"balance": inv.get("balance"), "units": len(inv.get("units") or []),
                     "headroom": plan["headroom"], "excluded": reasons, "ingest": ing, "card_ok": card_ok,
                     "summary_built_at": summary.get("built_at")},
             memory_refs=refs, rules=plan["rules"], choice=plan["buy"],
             why=f"{len(plan['buy'])} lines, worst cost {plan['worst_cost']} of headroom {plan['headroom']}",
             elapsed_ms=int((time.time() - t0) * 1000))
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
