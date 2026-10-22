"""LAB 1: choose by experience/reviews, negotiate, remember, and leave a review."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from negotiation import negotiate
from shop_catalog import load_shops
from purchase_policy import compare_shops
from trade_memory import evaluate_trade, load_memory, remember


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", required=True, type=Path)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    parser.add_argument("--compare-shops", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--memory", type=Path, default=Path(os.environ.get(
        "MARKET_BUYER_MEMORY", str(Path(__file__).with_name("buyer-memory.json")))))
    args = parser.parse_args()
    if args.budget <= 0:
        parser.error("budget must be positive")
    try:
        shops = load_shops(args.shops)
        memory = load_memory(args.memory)
        shop, card, comparison = compare_shops(shops, memory, args.item, args.budget,
                                               limit=args.compare_shops)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print('[compare] ' + json.dumps(comparison, ensure_ascii=False), flush=True)
    if shop is None:
        # A failed public lookup is a connection failure, not dishonesty.
        attempted = comparison['comparisons']
        fallback = next((s for s in shops if attempted and s['name'] == attempted[0]['name']), None)
        review = 'No reachable trusted candidate; no purchase attempted.'
        if fallback is not None:
            try:
                remember(args.memory, shop_name=fallback['name'], item=args.item, budget=args.budget,
                         receipt=None, outcome='unavailable', stars=2, review=review)
            except (OSError, ValueError) as error:
                print(f'[memory-error] {error}; previous memory retained', file=sys.stderr)
        print(json.dumps({'shop': fallback['url'] if fallback else '', 'receipt': None,
                          'stars': 2, 'review': review}, ensure_ascii=False), flush=True)
        return
    print(f"[choose] {shop['name']}: {comparison['reason']}; url={shop['url']}", flush=True)

    # Bound the context size; preserve review ordering (newest first).
    reviews = [{"stars": r["stars"], "round": r["round"], "review": r["review"][:600]}
               for r in shop["reviews"][:10]]
    personal = [{k: row.get(k) for k in ("item", "budget", "price", "outcome", "stars")}
                for row in memory if row["shop_name"] == shop["name"] and row.get('item') == args.item][-5:]
    context = json.dumps({"shop_name": shop["name"], "reviews": reviews,
                          "personal_trades": personal, "price_comparison": comparison}, ensure_ascii=False)
    try:
        trade = negotiate(shop["url"], args.item, args.budget, context=context, prefetched_card=card)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError,
            StopIteration, subprocess.SubprocessError) as error:
        trade = {"receipt": None, "error": f"{type(error).__name__}: {str(error)[:240]}"}
        print(f"[trade-error] {trade['error']}", flush=True)
        if isinstance(error, subprocess.SubprocessError) or str(error).startswith("harness exit"):
            # Our harness failure is not evidence that the shop is dishonest.
            trade = {"receipt": None, "state": "BUYER_HARNESS_ERROR"}

    stars, outcome, review = evaluate_trade(trade, item=args.item, budget=args.budget)
    if trade.get("state") == "BUYER_HARNESS_ERROR":
        review = "Buyer harness failed; no fault attributed to the shop."
    receipt = trade.get("receipt") if trade.get("state") == "TASK_STATE_COMPLETED" else None
    try:
        remember(args.memory, shop_name=shop["name"], item=args.item, budget=args.budget,
                 receipt=receipt, outcome=outcome, stars=stars, review=review)
        print(f"[memory] saved {outcome} for {shop['name']} in {args.memory}", flush=True)
    except (OSError, ValueError) as error:
        print(f"[memory-error] {error}; previous memory retained", file=sys.stderr, flush=True)
    # Keep the receipt data Part unchanged; the kit checks the signature.
    print(json.dumps({"shop": shop["url"], "receipt": receipt,
                      "stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
