"""Kit restock hook: carries out the operator's restock plan mechanically (human code, not editable by the model).

    python3 -m kernel.restock --state <.market path> [--root R]

Hook contract (kit agent.md): the kit runs this every 60 s and reads the last printed line, {"buy": [{"sku", "qty"}]}
with qty 1-20. It buys at most 10 lines per run, one supply call per 10 s, and stops at the first line the balance
cannot pay. Outside the restock window it waits up to 90 s per line, then skips it.

What to buy is the operator's decision, written to <root>/a2/restock_plan.json as {"buy": [...], "valid_until": ts}.
This hook only checks it: a missing, expired or already bought plan, or a window that is not open yet, prints
{"buy": []}; otherwise guard() applies the safety rules (DESIGN 10: no purchase may take the balance below the
bankrupt line). It never prints "bankrupt": true; filing bankruptcy is the kit's own path and stays outside the kernel.
Each run appends one trajectory row of kind "restock". Stdlib only.
"""

import argparse
import json
import math
import os

from kernel import common as c
from kernel.trajectory import Trajectory

MAX_LINES = 10
MAX_QTY = 20
KIT_WAIT_S = 90


def _num(v):
    return (
        float(v)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        else None
    )


def _catalog(raw):
    if isinstance(raw, list):
        return {x.get("sku"): x for x in raw if isinstance(x, dict)}
    return raw if isinstance(raw, dict) else {}


def unit_cost(sku, inventory, catalog):
    """Estimated cost of one unit, or None when nothing is known.

    Catalog "cost" if present, else the cost of our most recently supplied unit of that SKU, else the catalog
    list_price. This is a conservative estimate: supply costs run at a fraction of list price by grade, so list price
    overstates them. Surge pricing can raise the cost, so when inventory "surge" carries a factor above 1 for the SKU,
    the higher (surged) value is used.
    """
    item = catalog.get(sku) or {}
    est = _num(item.get("cost"))
    if est is None:
        units = [
            u
            for u in inventory.get("units") or []
            if isinstance(u, dict) and u.get("sku") == sku and _num(u.get("cost"))
        ]
        if units:
            last = max(
                enumerate(units), key=lambda iu: (iu[1].get("supplied_at") or 0, iu[0])
            )[1]
            est = _num(last.get("cost"))
    if est is None:
        est = _num(item.get("list_price"))
    if est is None:
        return None
    for s in inventory.get("surge") or []:
        f = _num(s.get("factor")) if isinstance(s, dict) else None
        if f and s.get("sku") == sku:
            est = max(est, est * f)
    return est


def guard(plan, inventory, catalog):
    """{"buy": [...]} that is safe to hand to the kit. Safety only, no strategy.

    Keeps at most MAX_LINES lines, clamps qty to 1..MAX_QTY ints, drops SKUs not in the catalog or with no cost
    estimate, and cuts quantities so the cumulative estimated cost never takes the balance below bankrupt_line.
    Without a balance or a bankrupt line nothing is bought.
    """
    catalog = _catalog(catalog)
    inventory = inventory if isinstance(inventory, dict) else {}
    balance, line = inventory.get("balance"), inventory.get("bankrupt_line")
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in (balance, line)
    ):
        return {"buy": []}
    room = balance - line
    if not math.isfinite(room):
        return {"buy": []}
    out = []
    for x in (plan or {}).get("buy") or []:
        if len(out) >= MAX_LINES:
            break
        if not isinstance(x, dict) or x.get("sku") not in catalog:
            continue
        q = x.get("qty")
        if not isinstance(q, (int, float)) or isinstance(q, bool):
            continue
        q = max(1, min(MAX_QTY, int(q)))
        cost = unit_cost(x["sku"], inventory, catalog)
        if cost is None:
            continue
        q = min(q, int(room // cost)) if room > 0 else 0
        if q < 1:
            continue
        room -= q * cost
        out.append({"sku": x["sku"], "qty": q})
    return {"buy": out}


def window_state(inventory, now):
    """("open" | "opens_soon" | "closed", seconds until it opens or None)."""
    w = (inventory or {}).get("restock") or {}
    if w.get("open"):
        return "open", 0
    opens = w.get("opens_at")
    if isinstance(opens, (int, float)) and not isinstance(opens, bool):
        wait = opens - now
        return ("opens_soon" if wait <= KIT_WAIT_S else "closed"), round(wait)
    return "closed", None


def plan_id(plan_path, plan):
    """Identity of one written plan: rewriting the file, even with the same lines, makes a new plan."""
    if not isinstance(plan, dict):
        return None
    return f"{os.stat(plan_path).st_mtime_ns}:{plan.get('valid_until')}"


def run(root, state, now=None):
    """One hook run: returns the {"buy": [...]} to print and appends the trajectory row."""
    now = c.now_ts() if now is None else now
    plan_path = os.path.join(root, "a2/restock_plan.json")
    plan = c.load_json(plan_path)
    inv = c.load_json(os.path.join(state, "inventory.json"), {}) or {}
    win, opens_in = window_state(inv, now)
    age = round(now - os.path.getmtime(plan_path)) if isinstance(plan, dict) else None
    lines_in = (
        len(plan.get("buy") or [])
        if isinstance(plan, dict) and isinstance(plan.get("buy"), list)
        else 0
    )
    valid = plan.get("valid_until") if isinstance(plan, dict) else None
    # a plan is bought once: the kit runs this every 60 s and an open window spans several runs, so without this the
    # same plan was bought 7 times on 2026-11-06 04:40-05:03 and the balance fell from 60.5M to 0.45M
    done_path = os.path.join(root, "a2/restock_done.json")
    pid = plan_id(plan_path, plan)
    out, cost = {"buy": []}, 0.0
    if not isinstance(plan, dict):
        why = "no_plan"
    elif not isinstance(valid, (int, float)) or now > valid:
        why = "expired"
    elif win == "closed":
        why = "window_closed"
    elif win == "opens_soon":
        # the kit does not wait for the window: its first supply call gets 429 and that line is skipped (live 11-06
        # 06:21 and 07:21), and a bought-once plan never retries it. The window lasts 180 s, so an open run follows
        why = "opens_soon"
    elif (c.load_json(done_path, {}) or {}).get("plan_id") == pid:
        why = "executed"
    else:
        catalog = _catalog(c.load_json(os.path.join(state, "catalog.json"), {}))
        out = guard(plan, inv, catalog)
        cost = sum(unit_cost(x["sku"], inv, catalog) * x["qty"] for x in out["buy"])
        why = "guarded"
        if out["buy"]:
            tmp = done_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"plan_id": pid, "ts": now, "buy": out["buy"]}, f)
            os.replace(tmp, done_path)
    login = c.owner_login(root)
    Trajectory(
        os.path.join(root, "a2/trajectory.jsonl"), (login,) if login else ()
    ).append(
        "kernel",
        "restock",
        why=why,
        plan_age_s=age,
        lines_in=lines_in,
        lines_out=len(out["buy"]),
        est_cost=round(cost),
        window=win,
        opens_in_s=opens_in,
    )
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default=".market")
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    print(json.dumps(run(c.data_root(a.root), a.state)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
