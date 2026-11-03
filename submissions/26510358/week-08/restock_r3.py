"""Round 3 restock hook (the kit runs it every 60 s): keeps memory fresh, starts the forum agent when due, and
prints what to buy.

    python3 restock_r3.py --state {state}

For each category:
  rate   = market-wide settles per hour, decayed (memory.category_demand), plus forum want-to-buy signals
  share  = 1 / (1 + competitors holding stock in that category)
  target = ceil(rate * share * HORIZON_H) units, capped for short-life food by half its shelf life
Categories without evidence get one unit for coverage (long-life food, cheap goods), within EXPLORE_SHARE of the
stock budget, so orders can find us while the ledger fills. No buying in the last STOP_BUYING_HOURS.

The plan itself is rule-based and answers within the hook's time. A background ReAct review
(restock_review_r3.py, every RESTOCK_REVIEW_EVERY_MIN) may scale a category's demand target by a clamped factor
(0 = skip it, also for coverage), e.g. after poor sell-through or near-expiry stock; factors expire after
ADJUST_TTL_H, so the rules take over again if the review stops.
"""
import argparse
import base64
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from r3lib import config, market, pricing
from r3lib.memory import Memory

FORUM_EVERY = 10 * 60


def active_adjustments(mem, now=None):
    a = mem.get("restock_adjust") or {}
    if (now or time.time()) - (a.get("ts") or 0) > config.ADJUST_TTL_H * 3600:
        return {}
    return a.get("items") or {}


def my_pubkey():
    try:
        key = serialization.load_pem_private_key((config.ROOT / "shop_key.pem").read_bytes(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()
    except (OSError, ValueError):
        return None


def kick(mem, script, key, every_s):
    """Start a background job (forum agent, restock review) at most once per every_s."""
    if time.time() - (mem.get(key) or 0) < every_s:
        return
    mem.put(key, time.time())
    log = open(config.STATE_DIR / f"{Path(script).stem}-run.log", "a")
    subprocess.Popen([sys.executable, str(config.ROOT / script)], cwd=config.ROOT, stdout=log, stderr=log,
                     start_new_session=True, env={k: v for k, v in os.environ.items() if k != "MARKET_OWNER"})


def kick_forum(mem):
    kick(mem, "forum_r3.py", "forum_kick", FORUM_EVERY)


def unit_cost_estimate(info):
    return info["list_price"] * 0.75            # worst case (grade A top): the market refuses a lot above balance


def pick_sku(category, info_by_cat, mem, median_price, explore):
    """Best-selling SKU in the category (memory), else the SKU closest to the category median price, which sits
    inside most orders' acceptable band (0.5-1.5 x a reference price)."""
    skus = info_by_cat.get(category) or []
    counts = mem.sku_counts(category)
    sold = [s for s in counts if any(x == s for x, _ in skus)]
    if sold and not explore:
        return sold[0]
    pool = [(s, i) for s, i in skus if not explore or
            (i.get("kind") == "food" and (i.get("shelf_hours") or 0) >= config.MIN_FOOD_SHELF_FOR_EXPLORE) or
            (i.get("kind") == "goods" and i["list_price"] <= config.EXPLORE_MAX_LIST)]
    if not pool:
        return None
    return min(pool, key=lambda x: abs(x[1]["list_price"] - (median_price or x[1]["list_price"])))[0]


def plan(inv, demand_rows, mem, now=None, dry_run=False, adjust=None):
    now = now or time.time()
    adjust = active_adjustments(mem, now) if adjust is None else adjust
    cat = market.catalog()
    info_by_cat = {}
    for s, i in cat.items():
        info_by_cat.setdefault(i["category"], []).append((s, i))
    units = inv.get("units") or []
    have, stock_cost, cat_cost = {}, 0, {}
    for u in units:
        if u.get("grade") != "expired":
            have[u["category"]] = have.get(u["category"], 0) + 1
            stock_cost += u["cost"]
            cat_cost[u["category"]] = cat_cost.get(u["category"], 0) + u["cost"]
    cat_cap = config.CATEGORY_MAX_SHARE * config.MAX_STOCK_COST
    balance = inv.get("balance") or 0
    budget = min(balance - config.CASH_RESERVE, config.MAX_STOCK_COST - stock_cost)
    explore_budget = min(budget, config.EXPLORE_SHARE * config.MAX_STOCK_COST - (mem.get("explore_spent") or 0))
    dem = mem.category_demand(now=now)
    forum = mem.forum_demand_signals()
    lines, why = [], []

    # 1) evidence: categories that sell, by expected profit per hour
    needs = []
    for row in demand_rows:
        k = row["category"]
        d = dem.get(k) or {}
        rate = d.get("rate", 0) + 0.05 * forum.get(market.leaf(k), 0)
        if rate <= 0:
            continue
        share = 1 / (1 + (d.get("shops") or 0))
        exp_h = rate * share
        factor = (adjust.get(k) or {}).get("factor", 1.0)
        target = math.ceil(exp_h * config.HORIZON_H * factor)
        shelf = [i.get("shelf_hours") for _, i in info_by_cat.get(k, []) if i.get("shelf_hours")]
        if row.get("kind") == "food" and shelf and min(shelf) < 720:
            target = min(target, math.ceil(exp_h * min(shelf) * 0.5 * factor))
        ratio, n = mem.price_ratio(category=k)
        if ratio is not None and ratio < 0.65:
            why.append({"category": k, "skip": f"market pays {ratio:.2f} of list, no margin over cost"})
            continue
        need = target - have.get(k, 0)
        if factor != 1.0:
            why.append({"category": k, "review_factor": factor, "target": target})
        if need > 0:
            margin = (ratio or config.EV_DEFAULT_PRICE_RATIO) - config.EV_COST_RATIO
            needs.append((exp_h * row.get("median_price", 0) * margin, k, need, row, exp_h * factor, ratio))
    needs.sort(key=lambda x: -x[0])                       # expected margin per hour, not revenue
    for score, k, need, row, mu_h, ratio in needs:
        sku = pick_sku(k, info_by_cat, mem, row.get("median_price"), explore=False)
        if not sku:
            continue
        info = cat[sku]
        L = info["list_price"]
        cost = L * config.EV_COST_RATIO
        price = L * (ratio or config.EV_DEFAULT_PRICE_RATIO)
        loss = pricing.hold_loss(info.get("kind"), cost, info.get("shelf_hours"))
        ev_qty, evs = pricing.ev_quantity(have.get(k, 0), mu_h * config.HORIZON_H, price, cost, loss)
        room = int(max(0, cat_cap - cat_cost.get(k, 0)) // unit_cost_estimate(info))
        qty = min(20, need, ev_qty, room)
        if qty <= 0:
            why.append({"category": k, "sku": sku, "skip": "no unit with positive expected profit" if ev_qty <= 0
                        else f"category stock cost {cat_cost.get(k, 0):.0f} at the {config.CATEGORY_MAX_SHARE:.0%} cap",
                        "rule_need": need, "ev_qty": ev_qty})
            continue
        est = unit_cost_estimate(cat[sku]) * qty
        while qty > 1 and est > budget:
            qty -= 1
            est = unit_cost_estimate(cat[sku]) * qty
        if est > budget or len(lines) >= 10:
            continue
        budget -= est
        lines.append({"sku": sku, "qty": qty})
        cat_cost[k] = cat_cost.get(k, 0) + est
        why.append({"category": k, "sku": sku, "qty": qty, "reason": "demand", "score": round(score),
                    "rule_need": need, "ev_qty": ev_qty, "ev_first_units": evs[:3], "cap_room": room})

    # 2) coverage: one unit where we have nothing and there is no evidence yet, most SKUs first
    spent = 0
    for row in sorted(demand_rows, key=lambda r: -r.get("skus", 0)):
        k = row["category"]
        if len(lines) >= 10 or have.get(k) or (dem.get(k) or {}).get("n") or \
                (adjust.get(k) or {}).get("factor", 1.0) == 0:
            continue
        sku = pick_sku(k, info_by_cat, mem, row.get("median_price"), explore=True)
        if not sku:
            continue
        est = unit_cost_estimate(cat[sku])
        if est > min(budget, explore_budget - spent):
            continue
        spent += est
        budget -= est
        lines.append({"sku": sku, "qty": 1})
        why.append({"category": k, "sku": sku, "qty": 1, "reason": "coverage"})
    if spent and not dry_run:
        mem.put("explore_spent", (mem.get("explore_spent") or 0) + spent)
    return lines, why, {"balance": balance, "stock_cost": stock_cost, "units": len(units)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(config.MARKET_DIR))
    a = ap.parse_args()
    md = Path(a.state)
    mem = Memory()
    try:
        mem.sync_ledger()
        mem.sync_shops({my_pubkey()} - {None})
    except Exception as e:
        print(json.dumps({"event": "memory_sync_error", "error": str(e)[:200]}), flush=True)
    try:
        kick_forum(mem)
        if (market.read_json("inventory.json", {}, md) or {}).get("round", 0) >= 3:
            kick(mem, "restock_review_r3.py", "review_kick", config.RESTOCK_REVIEW_EVERY_MIN * 60)
    except Exception as e:
        print(json.dumps({"event": "forum_kick_error", "error": str(e)[:200]}), flush=True)
    inv = market.read_json("inventory.json", {}, md) or {}
    out = {"buy": []}
    if (inv.get("round") or 0) >= 3:
        if (inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0) and not inv.get("units") \
                and (inv.get("bankruptcies_left") or 0) > 0:
            out["bankrupt"] = True
        elif config.hours_left_in_round() > config.STOP_BUYING_HOURS:
            lines, why, state = plan(inv, market.read_json("demand.json", [], md) or [], mem)
            out["buy"] = lines
            if lines:
                mem.record_restock(lines, why)
                cat = market.catalog()
                for ln in lines:
                    cost = (cat.get(ln["sku"]) or {}).get("list_price", 0) * config.EV_COST_RATIO * ln["qty"]
                    if cost >= 300_000:                  # big bets are worth remembering when they go wrong
                        mem.note("large_restock", ln["sku"], f"asked for {ln['qty']} x {ln['sku']} "
                                 f"(~{cost:,.0f} at expected cost)", 0.5)
                config.LOG_DIR.mkdir(parents=True, exist_ok=True)
                with open(config.LOG_DIR / "restock.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": round(time.time()), **state, "buy": lines, "why": why},
                                       ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
