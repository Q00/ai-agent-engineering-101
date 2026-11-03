"""The purchasing manager: what to buy from the market's supply. Rules only, no LLM.

    python restock.py --state <.market dir>        (the kit calls this every 60 s from Round 3)

Prints one JSON line: {"buy": [{"sku", "qty"}], "why": [...]} and "bankrupt": true when stuck.
The kit's default buys random SKUs in 3 random categories once fewer than 20 units are left. This one:
  1. keeps a cash reserve and spends at most a small share of the balance per run;
  2. scores each category by demand (settled sales in the last hours, from market_intel: the ledger's only
     public signal of what sells) against supply (units other shops and we already hold);
  3. in a category picks the SKU that sold most often, else the one priced nearest the category median,
     skipping food with a short shelf life and goods too expensive to hold (storage 1%/day).
Supply opens for everyone for 3 minutes every 20 minutes (inventory.json `restock`): outside a window the run
only updates the intel and the forum and buys nothing, so the kit never waits on a closed window.
Every number comes from policy.json.
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.environ.get("MARKET_DIR", ".market"))
    ap.add_argument("--offline", action="store_true", help="do not call the market (tests)")
    a = ap.parse_args()
    md = Path(a.state)
    os.environ["MARKET_DIR"] = str(md)
    sys.path.insert(0, str(Path(__file__).parent))
    import market_intel
    import policy

    p = policy.get("restock")
    load = lambda n, default: json.loads((md / n).read_text(encoding="utf-8")) if (md / n).exists() else default
    inv = load("inventory.json", {})
    catalog = load("catalog.json", {})
    catalog = catalog if isinstance(catalog, dict) else {x["sku"]: x for x in catalog}
    demand_list = load("demand.json", [])
    out = {"buy": [], "why": []}

    if inv.get("round", 0) < 3:
        print(json.dumps(out))
        return
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    balance = inv.get("balance") or 0
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        print(json.dumps({"buy": [], "bankrupt": True, "why": ["below the line with nothing to sell"]}))
        return
    try:
        intel = market_intel.load() if a.offline else market_intel.update(p["window_hours"],
                                                                         me=os.environ.get("SHOP_NAME", "olstjr"))
    except Exception as e:                   # market unreachable: decide from what was read last time
        intel = market_intel.load()
        out["why"].append(f"ledger not read ({type(e).__name__}); using the last intel")
    try:                                     # the forum writer rides on this 60 s tick: no process of its own
        import forum
        out["forum"] = forum.tick(intel)
    except Exception as e:
        out["forum_error"] = f"{type(e).__name__}: {e}"
    window = inv.get("restock") or {}
    if window and not window.get("open"):    # supply opens for 3 min every 20 min; outside it the kit would wait
        wait = (window.get("opens_at") or 0) - time.time()       # up to 90 s per line and then skip the line
        if wait > 30:
            out["why"].append(f"supply window closed, opens in {wait / 60:.1f} min")
            print(json.dumps(out, ensure_ascii=False))
            return
    if len(units) >= p["target_units"]:
        out["why"].append(f"{len(units)} units in stock, target {p['target_units']}")
        print(json.dumps(out, ensure_ascii=False))
        return
    budget = min(balance - p["cash_reserve"], balance * p["max_spend_share"])
    if budget <= 0:
        out["why"].append(f"balance {balance} is at the cash reserve {p['cash_reserve']}")
        print(json.dumps(out, ensure_ascii=False))
        return

    orders, wanted = market_intel.demand(intel)
    ours_cat, ours_sku = {}, {}
    for u in units:
        k = market_intel.leaf(u.get("category"))
        ours_cat[k] = ours_cat.get(k, 0) + 1
        ours_sku[u["sku"]] = ours_sku.get(u["sku"], 0) + 1

    def ok(x):
        if not (p["min_list_price"] <= x["list_price"] <= p["max_list_price"]):
            return False
        return x["kind"] != "food" or (x.get("shelf_hours") or 0) >= p["food_min_shelf_hours"]

    scored = []
    demand_list = random.sample(demand_list, len(demand_list))   # ties (no data yet) spread at random
    for d in demand_list:
        k = d["item"]
        if ours_cat.get(k, 0) >= p["max_units_per_category"]:
            continue
        score = (orders.get(k, 0) + 1) / (1 + ours_cat.get(k, 0) + 0.2 * intel["stock"].get(k, 0))
        scored.append((score, d))
    scored.sort(key=lambda x: -x[0])

    for score, d in scored:
        if len(out["buy"]) >= p["categories_per_run"]:
            break
        skus = [x for x in catalog.values() if x["category"] == d["category"] and ok(x)
                and ours_sku.get(x["sku"], 0) < p["max_units_per_sku"]]
        if not skus:
            continue
        x = max(skus, key=lambda x: (wanted.get(x["sku"], 0), -abs(x["list_price"] - d["median_price"])))
        unit_cost = x["list_price"] * p["cost_estimate"]
        room = p["target_units"] - len(units) - sum(b["qty"] for b in out["buy"])   # never overshoot the target
        if room < 1:
            break
        qty = min(p["max_units_per_sku"] - ours_sku.get(x["sku"], 0), int(budget // unit_cost), room)
        if qty < 1:
            continue
        budget -= qty * unit_cost
        out["buy"].append({"sku": x["sku"], "qty": qty})
        out["why"].append(f"{d['item']}: {orders.get(d['item'], 0)} sales, others hold "
                          f"{intel['stock'].get(d['item'], 0)}, we hold {ours_cat.get(d['item'], 0)}; "
                          f"{x['sku']} list {x['list_price']}, sold {wanted.get(x['sku'], 0)} times")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
