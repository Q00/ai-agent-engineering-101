"""No-LLM stock planner for the official v4 restock hook. Prints buys, never buys directly.

Keep 40% of starting capital in cash (user setting), budget at the documented
worst supply cost, and count every stock unit against inventory caps. Restock
from our confirmed sales; reserve a small exploration allocation for new SKUs.
"""
import argparse
from collections import Counter
import fcntl
import math
from pathlib import Path
import json
import time

from feedback import append_event, atomic_json, digest, load_json, number
from inventory import Inventory, read_json
from sales_performance import update as update_sales
from shop_policy import policy_at

ROOT = Path(__file__).resolve().parent


def plan(inv, catalog, demand, memory, attempts, cfg, now):
    if (not number(inv.get("balance")) or not number(inv.get("start")) or inv["start"] <= 0
            or not isinstance(catalog, dict) or len(catalog) > 20000
            or not isinstance(demand, list) or len(demand) > 5000):
        raise ValueError("Incomplete inventory, catalog or category data")
    reserve = math.ceil(inv["start"] * cfg["cash_reserve_percent"] / 100)
    budget = max(0, math.floor(inv["balance"]) - reserve)
    slots = min(cfg["max_units_per_run"], max(0, cfg["max_stock_units"] - len(inv["units"])))
    held = Counter()
    categories_held = Counter()
    for u in inv["units"]:
        if not isinstance(u, dict) or not isinstance(u.get("sku"), str) or not isinstance(u.get("category"), str):
            raise ValueError("Unknown stock units must not bypass caps")
        held[u["sku"]] += 1
        categories_held[u["category"]] += 1
    categories = {d["category"] for d in demand if isinstance(d, dict) and isinstance(d.get("category"), str)}
    candidates = {}
    for sku, p in catalog.items():
        if (isinstance(p, dict) and isinstance(sku, str) and isinstance(p.get("category"), str)
                and p["category"] in categories and p.get("kind") in ("food", "goods")
                and type(p.get("list_price")) is int and p["list_price"] > 0):
            # Highest documented supply cost is 75% of list. Include one credit
            # of rounding headroom. Actual grade/cost is unknown until delivery.
            candidates[sku] = {**p, "worst_cost": (p["list_price"] * 75 + 99) // 100 + 1}
    sales = memory["sales"]
    sku_sales = Counter(s["sku"] for s in sales)
    category_sales = Counter(s["category"] for s in sales)
    tried = Counter(a["sku"] for a in attempts)
    buy, choices = Counter(), []
    remaining = budget

    def available():
        return [sku for sku, p in candidates.items()
                if held[sku] + buy[sku] < (cfg["max_food_units_per_sku"] if p["kind"] == "food" else cfg["max_units_per_sku"])
                and p["worst_cost"] <= remaining]

    def add(sku, reason):
        nonlocal remaining
        p = candidates[sku]
        buy[sku] += 1
        remaining -= p["worst_cost"]
        categories_held[p["category"]] += 1
        choices.append({"sku": sku, "reason": reason, "budgeted_cost": p["worst_cost"]})

    preferred_slots = max(0, slots - cfg["exploration_units"]) if sales else 0
    for _ in range(preferred_slots):
        eligible = [s for s in available() if sku_sales[s] or category_sales[candidates[s]["category"]]]
        if not eligible:
            break
        sku = min(eligible, key=lambda s: (-sku_sales[s], -category_sales[candidates[s]["category"]],
                                          held[s] + buy[s], candidates[s]["worst_cost"], s))
        add(sku, "own_settled_sku" if sku_sales[sku] else "own_settled_category")
    # With no sales evidence, all slots are small exploration buys. Once there
    # is evidence, unproven items never consume the performance allocation.
    explore_slots = min(slots - len(choices), cfg["exploration_units"] if sales else slots)
    for _ in range(explore_slots):
        eligible = [s for s in available() if held[s] + buy[s] == 0]
        if not eligible:
            break
        sku = min(eligible, key=lambda s: (tried[s], categories_held[candidates[s]["category"]],
                    -category_sales[candidates[s]["category"]], digest([int(now // 86400), s])))
        add(sku, "small_exploration")
    return {"buy": [{"sku": sku, "qty": qty} for sku, qty in buy.items()]}, {
        "cash_floor": reserve, "balance_observed": inv["balance"], "budgeted_cost": budget - remaining,
        "projected_min_balance": math.floor(inv["balance"]) - (budget - remaining),
        "choices": choices, "own_settled_sample": len(sales),
        "limits": "Plan at snapshot time; refunds/storage can change cash. No grade selection or bankruptcy."}


def run_once(root=ROOT, now=None):
    now = time.time() if now is None else now
    cfg = policy_at(root)["restock"]
    if not cfg["enabled"]:
        return {"buy": []}
    md = root / ".market"
    md.mkdir(exist_ok=True)
    with (md / "restock.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"buy": []}
        inv = Inventory(md).snapshot()
        if inv["round"] < 3:
            return {"buy": []}
        path = md / "restock-control.json"
        state = load_json(path) if path.exists() else {"round": inv["round"], "attempts": []}
        if not isinstance(state, dict) or not isinstance(state.get("attempts"), list) or len(state["attempts"]) > 500:
            raise ValueError("Invalid restock control; refusing to reset limits")
        if state.get("round") != inv["round"]:
            state = {"round": inv["round"], "attempts": []}
        last = state.get("last_run")
        if last is not None and not number(last):
            raise ValueError("Invalid restock interval state")
        if number(last) and now - last < cfg["interval_seconds"]:
            return {"buy": []}
        # Reserve the interval before parsing or emitting any plan, so failures
        # and restarts cannot produce repeated buys against the same snapshot.
        state["last_run"] = now
        atomic_json(path, state)
        demand_path = md / "demand.json"
        if not -5 <= now - demand_path.stat().st_mtime <= 120:
            raise ValueError("Category file is stale")
        catalog, demand = read_json(md / "catalog.json"), read_json(demand_path)
        memory = update_sales(root, inv["round"], now)
        attempts = []
        for row in state["attempts"]:
            if not isinstance(row, dict) or not number(row.get("time")) or not isinstance(row.get("sku"), str):
                raise ValueError("Malformed stock planning memory")
            if 0 <= now - row["time"] <= 7 * 86400:
                attempts.append(row)
        decision, evidence = plan(inv, catalog, demand, memory, attempts, cfg, now)
        state["attempts"] = (attempts + [{"time": now, "sku": row["sku"], "planned_qty": row["qty"]}
                                        for row in decision["buy"]])[-500:]
        # Persist before returning instructions to the kit. A crash may skip a
        # buy, but cannot knowingly emit the same plan a second time.
        atomic_json(path, state)
        append_event(root / "logs/restock-decisions.jsonl", {"time": now, "round": inv["round"],
                     "status": "planned", **decision, **evidence})
        return decision


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", type=Path, required=True)
    args = ap.parse_args()
    try:
        if args.state.resolve() != (ROOT / ".market").resolve():
            raise ValueError("State must be this agent's .market directory")
        result = run_once()
    except Exception as exc:
        append_event(ROOT / "logs/restock-decisions.jsonl", {"time": time.time(),
                     "status": "refused", "error": type(exc).__name__, "detail": str(exc)[:300]})
        result = {"buy": []}  # A custom hook never falls back to random supply.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
