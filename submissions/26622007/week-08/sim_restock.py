"""Does restock.py's loop work over days? A made-up market, not a forecast: it only compares the two rules.

    python sim_restock.py CATALOG.json DEMAND.json [days]

The hidden popularity (Zipf over SKUs), deal volume, my share and prices are my assumptions, and the demand model
assumes much the same world, so a win here is no evidence about the real market. It can catch a broken loop:
buying without end, spending past the cash line, stock that never turns over.
"""
import json
import random
import sys

import market_model as mm
import restock

DEALS_PER_HOUR, MY_SHARE, PRICE_RATIO, STEP_HOURS, RIVALS = 100, 0.15, 0.8, 0.5, 5
COST = {"A": (0.55, 0.75), "B": (0.40, 0.60), "C": (0.25, 0.45)}
ODDS = {"food": (0.25, 0.5, 0.25), "goods": (0.7, 0.2, 0.1)}


def run(catalog, demand, days, use_model, seed=1, **over):
    rng = random.Random(seed)
    skus = sorted(catalog)
    rng.shuffle(skus)
    weights = [1 / (i + 1) for i in range(len(skus))]
    cfg = {**mm.DEFAULTS, **restock.DEFAULTS, "round_ends_at": None, **over}
    now, balance, units, rows, lost, sold, revenue, spoiled, seq = 1800000000.0, 5000000.0, [], [], 0, 0, 0, 0.0, 0
    for step in range(int(days * 24 / STEP_HOURS)):
        now += STEP_HOURS * 3600
        for sku in rng.choices(skus, weights, k=int(DEALS_PER_HOUR * STEP_HOURS)):
            x, seq = catalog[sku], seq + 1
            price = round(x["list_price"] * PRICE_RATIO)
            mine = rng.random() < MY_SHARE
            have = next((u for u in units if u["sku"] == sku), None) if mine else None
            if mine and have is None:
                lost += 1
            if have:
                units.remove(have)
                balance += price
                revenue += price
                sold += 1
            shop = "me" if have else f"s{rng.randrange(RIVALS)}"
            rows.append({"k": "settle", "seq": seq, "ts": now, "buyer": f"b{rng.randrange(40)}", "order": str(seq), "sku": sku,
                         "amount": price, "shop": shop})
        for u in units:
            if u["kind"] == "goods":
                balance -= u["cost"] * 0.01 * STEP_HOURS / 24
        spoiled += sum(u["cost"] for u in units if u["expires_at"] and u["expires_at"] <= now)
        units = [u for u in units if not u["expires_at"] or u["expires_at"] > now]
        model = mm.estimate(rows, catalog, {"me-buyer"}, {"me"}, now, cfg) if use_model else None
        inv = {"balance": balance, "units": units}
        for b in restock.plan(inv, catalog, demand, cfg, model)["buy"]:
            x = catalog[b["sku"]]
            for _ in range(b["qty"]):
                lo, hi = COST[rng.choices("ABC", ODDS.get(x["kind"], ODDS["goods"]))[0]]
                cost = x["list_price"] * rng.uniform(lo, hi)
                balance -= cost
                units.append({"unit": str(rng.random()), "sku": b["sku"], "category": x["category"], "kind": x["kind"], "grade": "A",
                              "cost": cost, "expires_at": now + x["shelf_hours"] * 3600 if x.get("shelf_hours") else None})
    return {"balance_profit": round(balance - 5000000), "balance": round(balance), "stock_units": len(units),
            "stock_cost": round(sum(u["cost"] for u in units)), "sold": sold, "revenue": round(revenue),
            "lost_for_no_stock": lost, "spoiled_cost": round(spoiled)}


if __name__ == "__main__":
    cat = {x["sku"]: x for x in json.load(open(sys.argv[1], encoding="utf-8"))}
    dem = json.load(open(sys.argv[2], encoding="utf-8"))
    days = float(sys.argv[3]) if len(sys.argv) > 3 else 7
    for name, on in (("first-stock rule only", False), ("demand model", True)):
        print(name, run(cat, dem, days, on), flush=True)
