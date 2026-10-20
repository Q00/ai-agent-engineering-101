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

Step 2 (memory): memory is kept but not yet used; the shop is the first in the list,
stars and review are placeholders.
"""
import argparse
import json
import os

import httpx

import buyer
import harness

MEMORY = "memory.jsonl"


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


def choose(shops, item, budget, memory):
    """Pick one shop. Step 1: the first shop in the list."""
    shop = shops[0]
    return shop, "step-1 skeleton: first shop in the list"


def review(shop, outcome, budget):
    """Stars and review for the trade. Step 1: placeholders."""
    if outcome["receipt"]:
        return 3, f"Deal at {outcome['receipt'].get('price')} (budget {budget})."
    return 3, f"No deal ({outcome['end']})."


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

    stars, text = review(shop, outcome, args.budget)
    print(json.dumps({"shop": shop["url"], "receipt": outcome["receipt"], "stars": stars, "review": text},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
