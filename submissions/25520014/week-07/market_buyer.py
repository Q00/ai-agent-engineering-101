"""Week 07 market buyer: picks one shop from the market's list, trades with it, reports.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The kit writes {shops}: a list of {"name", "url", "reviews": [{"stars", "review", "round"}]},
newest review first. The negotiation itself is the week-06 buyer (buyer.negotiate), so the
budget guard and the A2A protocol are unchanged.

The last line of the output is the result the kit sends to the market:
{"shop": <url from shops.json>, "receipt": <the shop's receipt data Part, or null>,
 "stars": 1-5, "review": "..."}

Step 1 (skeleton): the shop is the first in the list, stars and review are placeholders.
"""
import argparse
import json

import httpx

import buyer
import harness


def choose(shops, item, budget):
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
    shop, why = choose(shops, args.item, args.budget)
    print(f"[choose] {shop['url']} ({shop['name']}): {why}", flush=True)

    try:
        outcome = buyer.negotiate(shop["url"], args.item, args.budget)
    except (buyer.A2AError, harness.HarnessError, httpx.HTTPError) as e:
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        outcome = {"receipt": None, "end": f"error: {type(e).__name__}", "asks": [], "offers": []}

    stars, text = review(shop, outcome, args.budget)
    print(json.dumps({"shop": shop["url"], "receipt": outcome["receipt"], "stars": stars, "review": text},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
