"""No-LLM stock planner: local economics plus one bounded public ledger GET.

Keep 40% of starting capital in cash, budget worst-case supply cost and count
all inventory against caps. Print instructions; the official kit buys stock.
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
from sales_performance import sku_metrics, update as update_sales
from market_evidence import demand_scores, refresh as refresh_market
from opening_strategy import update as update_openings
from shop_policy import policy_at
from shop_horizon import phase as round_phase, purchase_window
from stock_budget import StockBudget
from stock_lifecycle import update as update_stock, metrics as stock_metrics

ROOT = Path(__file__).resolve().parent
WINDOW_HANDOFF_SECONDS = 10
SUPPLY_LINE_SECONDS = 10


def supply_window(inv, now):
    """Use the kit's explicit supply schedule, never infer a 20-minute clock."""
    raw = inv.get("restock")
    if not isinstance(raw, dict) or type(raw.get("open")) is not bool:
        return {"status": "window_invalid"}
    dates = {k: raw[k] for k in ("opens_at", "closes_at")
             if number(raw.get(k)) and raw[k] >= 0}
    if raw["open"] is False:
        return {"status": "window_closed", **dates}
    if (len(dates) != 2 or dates["closes_at"] <= dates["opens_at"]
            or not number(now)):
        return {"status": "window_invalid", **dates}
    status = ("window_closed" if now < dates["opens_at"] or now >= dates["closes_at"]
              else "window_closing" if dates["closes_at"] - now <= WINDOW_HANDOFF_SECONDS
              else "window_open")
    return {"status": status, **dates}


def _skip(root, path, state, window, now, status=None):
    """One diagnostic per state transition, rather than one row every hook."""
    event = {**window, "status": status or window["status"]}
    if state.get("last_skip") != event:
        state["last_skip"] = event
        atomic_json(path, state)
        append_event(root / "logs/restock-decisions.jsonl", {
            "time": now, "round": state["round"], **event, "buy": []})
    return {"buy": []}


def plan(inv, catalog, demand, memory, attempts, cfg, now, public=None, *, lifecycle=None, horizon=None):
    if (not number(inv.get("balance")) or not number(inv.get("start")) or inv["start"] <= 0
            or not isinstance(catalog, dict) or len(catalog) > 20000
            or not isinstance(demand, list) or len(demand) > 5000):
        raise ValueError("Incomplete inventory, catalog or category data")
    reserve = math.ceil(inv["start"] * cfg["cash_reserve_percent"] / 100)
    budget = max(0, math.floor(inv["balance"]) - reserve)
    exposure = StockBudget(inv, cfg)
    # The live hook always supplies the validated round schedule. Pure callers
    # may omit it when testing the independent capital/selection calculations.
    stage = (round_phase(horizon, inv.get("round"), now) if horizon is not None else
             {"phase": "unscheduled_calculation", "allow_purchases": True, "allow_exploration": True})
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
    economics = sku_metrics(memory, now)
    lifecycle_stats = stock_metrics(lifecycle, now) if lifecycle is not None else {}
    popular = demand_scores(public, now)
    slow = {u["sku"] for u in inv["units"] if number(u.get("supplied_at"))
            and now - u["supplied_at"] >= cfg["slow_stock_hours"] * 3600}
    paused = slow | {sku for sku, m in economics.items() if m["mature"] >= cfg["min_mature_sales"]
                     and (m["net_estimate"] <= 0 or m["refunded"] / m["settled"] >= .5)}
    # A vanished unit is not a confirmed sale or spoilage. Pause new stock of
    # that SKU while the separate lifecycle record awaits settlement evidence.
    paused |= {sku for sku, m in lifecycle_stats.items()
               if m["unresolved_cost"] > 0 or m["held_expired_cost"] > 0}
    windows = {sku: purchase_window(horizon, inv.get("round"), p, economics.get(sku), now)
               for sku, p in candidates.items()} if horizon is not None else {}
    tried = Counter(a["sku"] for a in attempts)
    buy, choices = Counter(), []
    remaining = budget

    def stock_cap(sku):
        cap = cfg["max_food_units_per_sku"] if candidates[sku]["kind"] == "food" else cfg["max_units_per_sku"]
        m = economics.get(sku, {})
        proven = (m.get("mature", 0) >= cfg["min_mature_sales"]
                  and m.get("net_estimate", 0) > 0 and m.get("speed_score", 0) > 0
                  and not m.get("unknown", 0) and not m.get("refunded", 0))
        # Normal warranty exposure is expected for a shop that keeps selling.
        # It cannot establish profitability by itself, but it must not erase
        # three mature profitable observations. While it remains, limit depth
        # to two; incomplete costs/refunds still limit new exposure to one.
        cap = min(cap, 2 if proven and m.get("pending", 0) else 3 if proven else 1)
        return cap

    def available():
        return [sku for sku, p in candidates.items()
                if stage["allow_purchases"] and sku not in paused
                and held[sku] + buy[sku] < stock_cap(sku)
                and p["worst_cost"] <= remaining and exposure.reason(sku, p) is None
                and windows.get(sku, (True, None))[0]]

    def add(sku, reason):
        nonlocal remaining
        p = candidates[sku]
        exposure.add(sku, p)
        buy[sku] += 1
        remaining -= p["worst_cost"]
        categories_held[p["category"]] += 1
        choices.append({"sku": sku, "reason": reason, "budgeted_cost": p["worst_cost"]})

    exploration_slots = cfg["exploration_units"] if stage["allow_exploration"] else 0
    preferred_slots = max(0, slots - exploration_slots) if sales else 0
    for _ in range(preferred_slots):
        eligible = [s for s in available() if sku_sales[s] or category_sales[candidates[s]["category"]]]
        if not eligible:
            break
        def rank(s):
            m = economics.get(s, {})
            proven = m.get("mature", 0) >= cfg["min_mature_sales"] and m.get("net_estimate", 0) > 0
            return (-int(proven), -m.get("speed_score", 0) if proven else 0,
                    -sku_sales[s], -category_sales[candidates[s]["category"]],
                    held[s] + buy[s], candidates[s]["worst_cost"], s)
        sku = min(eligible, key=rank)
        add(sku, "profit_and_turnover" if economics.get(sku, {}).get("mature", 0) >= cfg["min_mature_sales"]
            else "limited_own_sample" if sku_sales[sku] else "own_settled_category")
    # With no sales evidence, all slots are small exploration buys. Once there
    # is evidence, unproven items never consume the performance allocation.
    explore_slots = min(slots - len(choices), exploration_slots if sales else slots) if stage["allow_exploration"] else 0
    for _ in range(explore_slots):
        eligible = [s for s in available() if held[s] + buy[s] == 0]
        if not eligible:
            break
        sku = min(eligible, key=lambda s: (-popular.get(s, 0), tried[s], categories_held[candidates[s]["category"]],
                    -category_sales[candidates[s]["category"]], digest([int(now // 86400), s])))
        add(sku, "public_demand_exploration" if popular.get(sku) else "small_exploration")
    status = (stage.get("reason", stage["phase"]) if not stage["allow_purchases"] else
              "cash_floor_no_stock" if budget == 0 and not inv["units"] else
              "cash_floor_hold" if budget == 0 else "planned" if choices else "no_eligible_stock")
    bankruptcy_eligible = (number(inv.get("bankrupt_line")) and inv["balance"] < inv["bankrupt_line"]
                           and type(inv.get("bankruptcies_left")) is int and inv["bankruptcies_left"] > 0)
    return {"buy": [{"sku": sku, "qty": qty} for sku, qty in buy.items()]}, {
        "cash_floor": reserve, "balance_observed": inv["balance"], "budgeted_cost": budget - remaining,
        "projected_min_balance": math.floor(inv["balance"]) - (budget - remaining),
        "choices": choices, "own_settled_sample": len(sales),
        "paused_skus": sorted(paused)[:20],
        "operation_status": status, "needs_attention": status == "cash_floor_no_stock",
        "bankruptcy_eligible": bankruptcy_eligible, "automatic_bankruptcy": False,
        "exposure": exposure.summary(), "horizon": stage,
        "depth_limits": {sku: stock_cap(sku) for sku in buy},
        "horizon_exclusions": dict(Counter(reason for allowed, reason in windows.values() if not allowed)),
        "stock_observations": {"held_cost": sum(m["held_cost"] for m in lifecycle_stats.values()),
                               "unresolved_cost": sum(m["unresolved_cost"] for m in lifecycle_stats.values()),
                               "expired_unconfirmed_cost": sum(m["expired_unconfirmed_cost"] for m in lifecycle_stats.values())},
        "wallet_profit": memory.get("wallet_profit"),
        "limits": "Sampled settlements/refunds; estimated storage/turnover. Missing units are uncertain losses, not proven spoilage; no automatic bankruptcy."}


def run_once(root=ROOT, now=None):
    now = time.time() if now is None else now
    started = time.monotonic()
    # Keep deterministic injected time, but still account for real network and
    # disk latency so an initially open window cannot authorize a late plan.
    clock = lambda: now + max(0, time.monotonic() - started)
    policy = policy_at(root)
    cfg = policy["restock"]
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
        if state.get("last_run") is not None and not number(state["last_run"]):
            raise ValueError("Invalid restock interval state")
        previous = state.get("last_window")
        if previous is not None and (not isinstance(previous, dict)
                or any(not number(previous.get(k)) or previous[k] < 0 for k in ("opens_at", "closes_at"))
                or previous["closes_at"] <= previous["opens_at"]):
            raise ValueError("Invalid supply window control; refusing to reset it")
        window = supply_window(inv, clock())
        if window["status"] != "window_open":
            return _skip(root, path, state, window, clock())
        # One plan per observed opening, including server extensions of the
        # same window. Legacy last_run never hides the first open-window hook.
        if previous and window["opens_at"] <= previous["opens_at"]:
            return _skip(root, path, state, window, clock(), "already_planned")
        demand_path = md / "demand.json"
        if not -5 <= clock() - demand_path.stat().st_mtime <= 120:
            raise ValueError("Category file is stale")
        catalog, demand = read_json(md / "catalog.json"), read_json(demand_path)
        public = refresh_market(root, inv["round"], now)
        # The network read can outlive the previous inventory snapshot.
        inv = Inventory(md).snapshot()
        checked = supply_window(inv, clock())
        if checked["status"] != "window_open":
            return _skip(root, path, state, checked, clock())
        if inv["round"] != state["round"] or checked["opens_at"] != window["opens_at"]:
            return _skip(root, path, state, checked, clock(), "window_changed")
        current = clock()
        if public and public.get("round") != inv["round"]:
            public = None
        memory = update_sales(root, inv["round"], current, public=public, inventory=inv)
        lifecycle = update_stock(root, inv, memory, current)
        try:
            update_openings(root, inv, memory, public, policy, current)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            append_event(root / "logs/opening-strategy.jsonl", {"time": now, "status": "unavailable",
                         "error": type(exc).__name__})
        attempts = []
        for row in state["attempts"]:
            if not isinstance(row, dict) or not number(row.get("time")) or not isinstance(row.get("sku"), str):
                raise ValueError("Malformed stock planning memory")
            if 0 <= current - row["time"] <= 7 * 86400:
                attempts.append(row)
        # The official kit submits at most one supply line per ten seconds.
        # Counting units here is conservative: repeated SKUs collapse to fewer
        # lines, while cold-start exploration may use one line per unit.
        line_capacity = max(0, int((checked["closes_at"] - clock()) // SUPPLY_LINE_SECONDS))
        run_cfg = {**cfg, "max_units_per_run": min(cfg["max_units_per_run"], line_capacity)}
        decision, evidence = plan(inv, catalog, demand, memory, attempts, run_cfg, current, public,
                                  lifecycle=lifecycle, horizon=policy["horizon"])
        latest = Inventory(md).snapshot()
        checked = supply_window(latest, clock())
        if checked["status"] != "window_open":
            return _skip(root, path, state, checked, clock())
        if latest["round"] != state["round"] or checked["opens_at"] != window["opens_at"]:
            return _skip(root, path, state, checked, clock(), "window_changed")
        if latest != inv:
            return _skip(root, path, state, checked, clock(), "inventory_changed")
        if decision["buy"] and checked["closes_at"] - clock() <= SUPPLY_LINE_SECONDS * len(decision["buy"]):
            return _skip(root, path, state, checked, clock(), "window_closing")
        state["attempts"] = (attempts + [{"time": current, "sku": row["sku"], "planned_qty": row["qty"]}
                                        for row in decision["buy"]])[-500:]
        # Persist before returning instructions to the kit. A crash may skip a
        # buy, but cannot knowingly emit the same plan a second time.
        state.update(last_run=clock(), last_window={k: checked[k] for k in ("opens_at", "closes_at")})
        state.pop("last_skip", None)
        atomic_json(path, state)
        append_event(root / "logs/restock-decisions.jsonl", {"time": clock(), "round": inv["round"],
                     "status": "planned", "window": checked,
                     "supply_lines": len(decision["buy"]), "window_unit_cap": run_cfg["max_units_per_run"],
                     **decision, **evidence})
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
