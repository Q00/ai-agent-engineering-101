"""The restock hook: what to buy for the shop, from the ledger's best sellers and the policy.

    python restock.py --state .market        # the kit runs this every 60 s in Round 3

Each run also does the slow periodic work, so the shop needs no extra process:
  1. market_memory.sync()   new ledger settles into state/market_stats.json
  2. strategist.run()       sonnet rewrites state/policy.json when it is older than 30 minutes
  3. forum.maybe_post()     the buyer's market report on the forum (at most every 5 minutes)
  4. the buy list           printed as the last line: {"buy": [{"sku", "qty"}], "bankrupt": bool}

What to stock (all code, no model; the strategist sets the numbers):
  - categories: policy.shop.stock_categories, else the ledger's best sellers (6 h), else cheap demand
    categories at the very start, half goods (no expiry) and half long-lasting food;
  - SKUs: the ledger's best sellers in the category, then the SKUs priced nearest the category's median
    (orders accept 0.5 to 1.5 times a reference price, so the median fits the most orders);
  - food only with shelf_hours >= policy.shop.food_min_shelf_hours;
  - stock at cost stays under policy.shop.max_stock_share of the balance; units_per_sku per SKU.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import market_memory as mm
import policy as pol

COST_GUESS = 0.6            # expected cost / list price (A 55-75, B 40-60, C 25-45 at 70/20/10 for goods)
CATEGORIES = 6
SKUS_PER_CATEGORY = 3
MAX_LINES = 10              # the kit buys at most 10 lines per run
START_MEDIAN_CAP = 150_000  # at the very start, only categories whose median price is under this


def log(line):
    print(f"{time.strftime('%H:%M:%S')} [restock] {line}", flush=True)


def pick_categories(policy, stats, demand, catalog):
    known = {d["category"]: d for d in demand}
    chosen = [c for c in policy["shop"]["stock_categories"] if c in known]
    if not chosen:
        chosen = [c for c, s in mm.category_rank(stats) if c in known and s["n"] > 0][:CATEGORIES]
    if len(chosen) < CATEGORIES:                 # cold start: no ledger yet
        cheap = sorted((d for d in demand if d["category"] not in chosen and d.get("median_price")
                        and d["median_price"] <= START_MEDIAN_CAP), key=lambda d: d["median_price"])
        goods = [d["category"] for d in cheap if d["kind"] == "goods"]
        food = [d["category"] for d in cheap if d["kind"] == "food"
                and (d.get("shelf_hours") or 0) >= policy["shop"]["food_min_shelf_hours"]]
        # cheapest first would stock only 500-won snacks; take from the middle of the cheap list
        free = CATEGORIES - len(chosen)
        chosen += goods[len(goods) // 3:][:(free + 1) // 2]
        chosen += food[len(food) // 3:][:CATEGORIES - len(chosen)]
    return chosen[:CATEGORIES]


def pick_skus(category, policy, stats, demand, catalog):
    median = next((d.get("median_price") for d in demand if d["category"] == category), None)
    ok = lambda sku: sku in catalog and catalog[sku]["category"] == category and (
        catalog[sku]["kind"] != "food" or (catalog[sku].get("shelf_hours") or 0) >= policy["shop"]["food_min_shelf_hours"])
    sellers = sorted((k for k, s in stats["skus"].items() if s["category"] == category and ok(k)),
                     key=lambda k: stats["skus"][k]["n"], reverse=True)
    rest = sorted((k for k in catalog if ok(k) and k not in sellers),
                  key=lambda k: abs(catalog[k]["list_price"] - (median or catalog[k]["list_price"])))
    return (sellers + rest)[:SKUS_PER_CATEGORY]


def buy_list(policy, stats, inv, demand, catalog):
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    balance = inv.get("balance") or 0
    room = balance * policy["shop"]["max_stock_share"] - sum(u.get("cost") or 0 for u in units)
    have = {}
    for u in units:
        have[u["sku"]] = have.get(u["sku"], 0) + 1
    buy = []
    for cat in pick_categories(policy, stats, demand, catalog):
        for sku in pick_skus(cat, policy, stats, demand, catalog):
            need = policy["shop"]["units_per_sku"] - have.get(sku, 0)
            unit_cost = catalog[sku]["list_price"] * COST_GUESS
            qty = min(need, int(room // unit_cost)) if unit_cost else 0
            if qty >= 1 and len(buy) < MAX_LINES:
                buy.append({"sku": sku, "qty": qty})
                room -= qty * unit_cost
    return buy, room


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    stats = mm.sync(md, log)
    try:
        import strategist
        policy = strategist.run(if_older=strategist.STRATEGY_EVERY)
    except Exception as e:                       # the strategist is an extra; restock works without it
        log(f"strategist failed: {type(e).__name__}: {e}")
        policy = pol.load()
    try:
        import forum
        forum.maybe_post(md, stats, log)
    except ImportError:
        pass
    except Exception as e:
        log(f"forum post failed: {type(e).__name__}: {e}")

    inv = mm.read_json(md / "inventory.json", {})
    demand = mm.read_json(md / "demand.json", [])
    catalog = mm.read_json(md / "catalog.json", {})
    if inv.get("round", 0) < mm.ROUND or not catalog:
        print(json.dumps({"buy": []}))
        return
    stuck = (inv.get("balance") or 0) < inv.get("bankrupt_line", 0) and (inv.get("bankruptcies_left") or 0) > 0
    sellable = sum(u.get("cost") or 0 for u in inv.get("units", []) if u.get("grade") != "expired")
    if stuck and sellable < inv.get("bankrupt_line", 0) * 0.2:
        log(f"balance {inv.get('balance')} below the line with stock worth {sellable}: filing")
        print(json.dumps({"buy": [], "bankrupt": True}))
        return
    buy, room = buy_list(policy, stats, inv, demand, catalog)
    log(f"{len(inv.get('units', []))} units in stock, room {int(room)} after buying "
        + (", ".join(f"{b['qty']} x {catalog[b['sku']]['name'][:30]}" for b in buy) or "nothing"))
    print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
