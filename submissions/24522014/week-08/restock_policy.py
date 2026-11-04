"""Bounded restock with periodic public benchmarks; no model calls."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import math
from uuid import uuid4
from market_intel import fresh
from r3_policy import load_policy
from forum_monitor import observe as observe_forum, REQUEST
from purchase_monitor import observe as observe_purchases
from strategy_policy import effective
from strategy_controller import run as improve_strategy
from forum_demand import requests as forum_requests
from restock_capacity import config as capacity_config, profitable_counts
from collections import Counter


def demand_hints(posts, catalog, now):
    """Recent buyer requests are weak signals; never execute instructions in forum text."""
    hints = {}
    for request in forum_requests(posts[-2000:], catalog, now):
        for sku in request["skus"]:
            hints.setdefault(sku, set()).add(str(request["author"]))
    return {sku: min(2, len(authors)) for sku, authors in hints.items()}


def plan(inventory, catalog, demand, sales, cursor=0, *, hints=None, now=None, intelligence=None, runtime=None, leader_hints=None, own_profitable=None, quality=None):
    now = time.time() if now is None else now
    hints = hints or {}
    intelligence = intelligence or {}
    leader_hints = leader_hints or {}
    own_profitable = own_profitable or {}
    quality = quality or {}
    limits = capacity_config()
    if inventory.get("round", 0) < 3:
        return [], {"reason": "not_round_3"}
    if (inventory.get("restock") or {}).get("open") is False:
        return [], {"reason": "market_restock_window_closed"}
    runtime = effective(now=now) if runtime is None else runtime
    if runtime["controls"]["restock_paused"]:
        return [], {"reason": "strategy_cash_or_data_guard", "strategy_version": runtime["version"]}
    units = [u for u in inventory.get("units", []) if u.get("grade") != "expired"]
    balance = max(0, inventory.get("balance", 0))
    end = datetime.fromisoformat(load_policy()["selling"]["round_end"]).timestamp()
    if now >= end - 86400:
        return [], {"reason": "round_end_purchase_stop"}
    rate = inventory.get("storage_per_day", 0.01)
    if type(rate) not in (int, float) or not 0 <= rate <= 1:
        rate = 0.01
    existing_storage = sum(max(0, u.get("cost", 0)) * rate * 7 for u in units if u.get("kind") == "goods")
    # Our seller offers no warranties; retain any explicitly recorded future liability.
    refund_reserve = max(0, inventory.get("local_refund_reserve", 0))
    reserve = max(500000, inventory.get("start", 5000000) * 0.2) + existing_storage + refund_reserve
    budget = max(0, min(balance * runtime["controls"]["restock_budget_rate"], balance - reserve))
    if len(units) >= limits["max_total_units"] or not budget:
        return [], {"reason": "stock_or_cash_limit", "budget": budget, "capacity": limits}
    categories = {d["category"] for d in demand}
    owned = Counter(u["sku"] for u in units)
    def signal(sku):
        return intelligence.get(sku, {})
    def proven(sku):
        s = signal(sku)
        return bool(sales.get(sku, 0) or own_profitable.get(sku, 0)
                    or (s.get("distinct_buyers", 0) >= 3 and s.get("settles_6h", 0) >= 3))
    def bulk_proven(sku):
        s = signal(sku)
        return (own_profitable.get(sku, 0) >= limits["min_profitable_own_sales_6h"]
                or (s.get("distinct_buyers_6h", 0) >= limits["min_public_buyers"]
                    and s.get("settles_6h", 0) >= limits["min_public_sales_6h"]))
    def sku_limit(sku):
        if quality.get(sku, {}).get("weak"):
            return 1
        return limits["max_proven_units_per_sku"] if bulk_proven(sku) else 1
    experimental = [u for u in units if not proven(u["sku"])]
    stale = any(type(u.get("supplied_at")) in (int, float)
                and now - u["supplied_at"] >= 86400 for u in units)
    exploration_slots = 0 if stale else max(0, limits["max_unproven_units"] - len(experimental))
    candidates = [(sku, value) for sku, value in sorted(catalog.items())
                  if owned[sku] < sku_limit(sku) and value.get("category") in categories
                  and type(value.get("list_price")) in (int, float) and math.isfinite(value["list_price"]) and value["list_price"] > 0]
    if candidates:
        offset = cursor % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]
    # Stable sort preserves rotating exploration order among equal observed-sale counts.
    def opportunity(pair):
        sku, product = pair
        s = signal(sku)
        cost = product["list_price"] * 0.75
        price = min(product["list_price"], s.get("median_price", product["list_price"]))
        gap = s.get("observed_gap_hours")
        hours = min(24, max(1, gap)) if type(gap) in (int, float) and math.isfinite(gap) else 24
        margin = price - cost - cost * rate * hours / 24
        factor = .9 if quality.get(sku, {}).get("weak") else 1.05 if quality.get(sku, {}).get("strong") else 1
        return (not proven(sku), -margin / cost / hours * factor, -sales.get(sku, 0), -leader_hints.get(sku, 0), -hints.get(sku, 0))
    candidates.sort(key=opportunity)
    buys = []
    estimated = 0
    storage_reserve = 0
    planned_units = 0
    for sku, value in candidates:
        # Supply draws the grade on arrival; shelf_hours is not a minimum remaining lifetime.
        if value.get("kind") == "food":
            continue
        if not proven(sku) and exploration_slots <= 0:
            continue
        worst_cost = value["list_price"] * 0.75
        reference = signal(sku).get("median_price")
        if reference is not None and reference <= worst_cost * (1 + rate):
            continue
        carrying_cost = worst_cost * rate * 7
        qty = min(sku_limit(sku)-owned[sku], limits["max_units_per_run"]-planned_units,
                  limits["max_total_units"]-len(units)-planned_units)
        while qty > 0 and estimated + storage_reserve + qty*(worst_cost+carrying_cost) > budget:
            qty -= 1
        if qty <= 0:
            continue
        buys.append({"sku": sku, "qty": qty})
        estimated += worst_cost * qty
        storage_reserve += carrying_cost * qty
        planned_units += qty
        if not proven(sku):
            exploration_slots -= qty
        if planned_units >= min(limits["max_units_per_run"], limits["max_total_units"] - len(units)):
            break
    return buys, {"reason": "bounded_experiment", "budget": budget,
                  "strategy_version": runtime["version"], "strategy_experiment": runtime.get("experiment"),
                  "estimated_max_cost": estimated, "cash_reserve": reserve,
                  "one_day_storage_reserve": storage_reserve/7,
                  "new_stock_seven_day_storage": storage_reserve, "planned_units": planned_units,
                  "capacity": limits, "own_profitable_sales_6h": own_profitable,
                  "quality_evidence": quality,
                  "unproven_stock": len(experimental), "stale_stock": stale,
                  "existing_seven_day_storage": existing_storage, "refund_reserve": refund_reserve,
                  "public_evidence_skus": len(intelligence),
                  "forum_hints": hints,
                  "leader_hints": leader_hints,
                  "food_supply": "deferred: no guaranteed shelf life or supported sales-window estimate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    md = args.state
    read = lambda name: json.loads((md / name).read_text(encoding="utf-8"))
    inv, catalog, demand = read("inventory.json"), read("catalog.json"), read("demand.json")
    # A short-lived hook activates forum monitoring without restarting the live kit.
    # The observer rate-limits itself to 30 minutes and performs no network/model calls.
    if inv.get("round") == 3:
        try:
            observe_forum(md)
            observe_purchases(md)
            from buyer_support import observe as observe_buyer_support
            observe_buyer_support(md)
        except (OSError, ValueError, TypeError):
            pass  # Forum observation must not stop inventory safety checks.
        try:
            from leader_monitor import observe as observe_leaders
            observe_leaders(md)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"[leaders] observation deferred: {type(error).__name__}: {error}", flush=True)
        try:
            from seller_quality import observe as observe_quality
            observe_quality(md)
            from warranty_trial import evaluate as evaluate_warranty
            evaluate_warranty(md)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            improve_strategy(md)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"[strategy] update deferred: {type(error).__name__}: {error}", flush=True)
        try:
            from forum_engagement import run as engage_forum
            engage_forum(md)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"[forum] engagement deferred: {type(error).__name__}: {error}", flush=True)
    memory_path = md / "r3-restock-memory.json"
    memory = read("r3-restock-memory.json") if memory_path.exists() else {"round": 3, "cursor": 0, "last_sale_seq": 0, "sales": {}}
    # The local kit log contains our own settled sales. Read a bounded tail, never infer sales from disappearing stock.
    log = md.parent / "logs" / "market-kit.log"
    observed = {}
    if log.exists():
        with log.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 1048576))
            tail = stream.read().decode("utf-8", errors="replace")
        names = {}
        for sku, product in catalog.items():
            names.setdefault(product.get("name"), []).append(sku)
        for item, seq in re.findall(r"\[acp\] settled (.+) at \d+, ledger #(\d+)", tail):
            sku = item if item in catalog else (names[item][0] if len(names.get(item, [])) == 1 else None)
            if int(seq) > memory["last_sale_seq"] and sku:
                observed[int(seq)] = sku
    snapshot = fresh(md)
    own = snapshot.get("competition", {}).get("own", {}).get("shops") or {}
    own_url = (own.get("card") or {}).get("url")
    for row in snapshot.get("rows", []):
        data = row.get("data") or {}
        if (row.get("round") == 3 and row.get("kind") == "settle" and own_url
                and data.get("shop") == own_url and data.get("item") in catalog
                and row["seq"] > memory["last_sale_seq"]):
            observed[row["seq"]] = data["item"]
    for sku in observed.values():
        memory["sales"][sku] = memory["sales"].get(sku, 0) + 1
    if observed:
        memory["last_sale_seq"] = max(observed)
    # Forum data is optional. A malformed snapshot must not broaden purchasing.
    try:
        buffer = read("forum-buffer.json") if (md / "forum-buffer.json").exists() else {}
        posts = buffer.get("posts", []) if 0 <= time.time()-buffer.get("captured_at", 0) <= 1800 else []
        posts = posts or (read("forum.json") if (md / "forum.json").exists() else [])
        hints = demand_hints(posts, catalog, time.time()) if isinstance(posts, list) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        hints = {}
    # Signed offers with a warranty reserve their entire price until the coverage ends.
    from warranty_trial import liability
    liabilities = liability(md)
    inv["local_refund_reserve"] = liabilities
    try:
        from leader_monitor import restock_hints
        leader_hints = restock_hints(md)
    except (OSError, ValueError, KeyError, TypeError):
        leader_hints = {}
    try:
        own_profitable = profitable_counts(md)
    except (OSError, ValueError, KeyError, TypeError):
        own_profitable = {}
    from seller_quality import fresh as fresh_quality
    buys, explanation = plan(inv, catalog, demand, memory["sales"], memory["cursor"], hints=hints,
                             intelligence=snapshot.get("skus", {}), runtime=effective(md), leader_hints=leader_hints,
                             own_profitable=own_profitable, quality=fresh_quality(md))
    memory["cursor"] += 1
    temporary = memory_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(memory_path)
    logs = md.parent / "logs" / "restock"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (logs / (stamp + "-" + uuid4().hex[:8] + ".json")).write_text(
        json.dumps({"buy": buys, "explanation": explanation, "new_settled_sales": len(observed),
                    "sales_memory": memory["sales"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"buy": buys}, ensure_ascii=False))


if __name__ == "__main__":
    main()
