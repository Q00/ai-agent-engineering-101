"""Week 07 market buyer: picks one shop from the market's list, trades with it, reports.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The kit writes {shops}: a list of {"name", "url", "reviews": [{"stars", "review", "round"}]},
newest review first. The negotiation itself is the week-06 buyer (buyer.negotiate), so the
budget guard and the A2A protocol are unchanged.

The last line of the output is the result the kit sends to the market:
{"shop": <url from shops.json>, "receipt": <the shop's receipt data Part, or null>,
 "stars": 1-5, "review": "..."}

Memory: every trade adds one line {"shop", "item", "price", "result"} to memory.jsonl,
shops remembered by name (a shop's URL changes when it restarts its kit). The market test
writes its shop list to .market/test-shops-*.json; those runs neither read nor write memory.

Choosing (epsilon-greedy): with probability EPSILON a random shop from the list. Otherwise
  1. the shop where memory says this item was bought cheapest,
  2. never a shop whose only memories are no-deals,
  3. among the rest, the highest average review stars (no reviews counts as 3), ties at random.

Stars and review, like a person would leave them: honesty first, then value against what
I usually pay (the average remembered deal price for the item, else the budget):
  receipt price or item differs from what was agreed -> 1, "Agreed X, receipt says Y."
  deal 5% or more below the usual price -> 5, within 5% -> 4, 5% or more above -> 3
  no deal -> 2 (the shop's last ask and my budget), shop did not answer -> 1
"""
import argparse
import json
import os
import random

import httpx

import buyer
import harness

MEMORY = "memory.jsonl"
EPSILON = 0.1   # chance of trying a random shop instead of the best one
NO_REVIEW_STARS = 3.0


def is_test(shops_path):
    return os.path.basename(shops_path).startswith("test-shops-")


def load_memory():
    if not os.path.exists(MEMORY):
        return []
    with open(MEMORY, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def remember(shop, item, outcome):
    rc = outcome["receipt"]
    entry = {"shop": shop["name"], "item": item,
             "price": round(float(rc["price"])) if rc and rc.get("price") is not None else None,
             "result": "deal" if rc else "no deal"}
    with open(MEMORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def avg_stars(shop):
    stars = [r["stars"] for r in shop.get("reviews", []) if isinstance(r.get("stars"), (int, float))]
    return (sum(stars) / len(stars), len(stars)) if stars else (NO_REVIEW_STARS, 0)


def choose(shops, item, budget, memory):
    """Pick one shop; returns (shop, why)."""
    if random.random() < EPSILON:
        return random.choice(shops), f"exploring: random shop (epsilon {EPSILON})"

    names = {s["name"] for s in shops}
    cheapest, dealt, failed = {}, set(), set()
    for m in memory:
        if m["shop"] not in names:
            continue
        if m["result"] == "deal":
            dealt.add(m["shop"])
            if m["item"] == item:
                cheapest[m["shop"]] = min(cheapest.get(m["shop"], m["price"]), m["price"])
        else:
            failed.add(m["shop"])

    if cheapest:
        name = min(cheapest, key=cheapest.get)
        shop = next(s for s in shops if s["name"] == name)
        return shop, f"memory: bought {item} here at {cheapest[name]}, the cheapest I remember"

    avoid = failed - dealt
    pool = [s for s in shops if s["name"] not in avoid] or shops
    best = max(avg_stars(s)[0] for s in pool)
    shop = random.choice([s for s in pool if avg_stars(s)[0] == best])
    stars, n = avg_stars(shop)
    return shop, (f"reviews: highest average {stars:.1f} stars from {n} reviews"
                  + (f", avoiding {len(avoid)} shops with only no-deals" if avoid else ""))


def usual_price(item, budget, memory):
    prices = [m["price"] for m in memory if m["item"] == item and m["result"] == "deal" and m["price"] is not None]
    if prices:
        usual = sum(prices) / len(prices)
        return usual, f"the {usual:.0f} I usually pay"
    return budget, f"my budget of {budget}"


def review(outcome, item, budget, memory):
    """Stars (1-5) and a short factual review for the trade."""
    rc = outcome["receipt"]
    if not rc:
        if outcome["end"].startswith("error"):
            return 1, "Shop did not answer."
        if outcome["asks"]:
            return 2, f"No deal on {item}: shop stayed at {outcome['asks'][-1]}, my budget was {budget}."
        return 2, f"No deal on {item}."

    price = round(float(rc.get("price", 0)))
    agreed = outcome["offers"][-1] if outcome["offers"] else None
    if rc.get("item") != item:
        return 1, f"Ordered {item}, receipt says {rc.get('item')}."
    if agreed is not None and price != agreed:
        return 1, f"Agreed {agreed}, receipt says {price}."

    usual, basis = usual_price(item, budget, memory)
    if price <= usual * 0.95:
        return 5, f"Fair deal: {item} at {price}, below {basis}."
    if price < usual * 1.05:
        return 4, f"{item.capitalize()} at {price}, about {basis}."
    return 3, f"{item.capitalize()} at {price}, above {basis}."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True, help="the kit's shop list (JSON)")
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    print(f"market buyer: {args.item}, budget {args.budget} (harness: claude -p --model {harness.MODEL})", flush=True)

    with open(args.shops, encoding="utf-8") as f:
        shops = json.load(f)
    test = is_test(args.shops)
    memory = [] if test else load_memory()
    print(f"[memory] {len(memory)} past trades" + (" (market test: memory off)" if test else ""), flush=True)
    shop, why = choose(shops, args.item, args.budget, memory)
    print(f"[choose] {shop['url']} ({shop['name']}): {why}", flush=True)

    try:
        outcome = buyer.negotiate(shop["url"], args.item, args.budget)
    except (buyer.A2AError, harness.HarnessError, httpx.HTTPError) as e:
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        outcome = {"receipt": None, "end": f"error: {type(e).__name__}", "asks": [], "offers": []}
    if not test:
        remember(shop, args.item, outcome)

    stars, text = review(outcome, args.item, args.budget, memory)
    print(json.dumps({"shop": shop["url"], "receipt": outcome["receipt"], "stars": stars, "review": text},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
