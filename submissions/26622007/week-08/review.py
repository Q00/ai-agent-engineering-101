"""The kit's review hook (Round 3, after each paid deal): stars and a review from what the market says I got.

    python review.py --deal .market/deal.json

It also keeps the settled deal in memory/settled.jsonl: .market/broken.jsonl later says only an item's name and
when it broke, so which shop sold it and under what warranty must be remembered here.
"""
import argparse
import json
import os
import sys
import time

SETTLED = os.environ.get("BUYER_SETTLED", "memory/settled.jsonl")


def rate(price, settled):
    """(stars, review) from the facts. Value is the SKU's list price, or 0 if the food did not last."""
    name, grade, value = settled.get("name") or settled.get("sku"), settled.get("grade"), settled.get("value")
    what = f"{name}, grade {grade}, paid {price}"
    if settled.get("claim_honest") is False:
        return 1, f"{what}: the shop claimed grade {settled.get('claim')}, the market says {grade}."
    if value == 0:
        return 1, f"{what}: it did not last the time this order needed."
    if not value:
        return 3, f"{what}; receipt matched."
    saved = (value - price) / value
    stars = 5 if saved >= 0.2 else 4 if saved >= 0.05 else 3 if saved >= 0 else 2
    return stars, f"{what}, list price {value}; settled as agreed."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    args = ap.parse_args()
    with open(args.deal, encoding="utf-8") as f:
        deal = json.load(f)
    settled, price = deal.get("settled") or {}, (deal.get("receipt") or {}).get("price")
    os.makedirs(os.path.dirname(SETTLED) or ".", exist_ok=True)
    with open(SETTLED, "a", encoding="utf-8") as f:
        order = deal.get("order") or {}
        # use_within_hours next to the market's expires_at: whether a food unit lasted as long as the order needed
        f.write(json.dumps({"time": int(time.time()), "order": order.get("order"), "price": price,
                            **{k: settled.get(k) for k in ("shop", "unit", "sku", "name", "grade", "claim", "claim_honest",
                                                           "expires_at", "warranty_hours", "value", "ledger")},
                            "kind": order.get("kind"), "use_within_hours": order.get("use_within_hours")},
                           ensure_ascii=False) + "\n")
    stars, review = rate(price, settled)
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False), flush=True)
    # after the result line: the deal as a buyer post (posts are 10% of the buyer's rank); never fails the hook
    try:
        import buyer_post        # here, not at the top: a broken import never costs the review line above
        line = buyer_post.maybe_post(os.path.dirname(os.path.abspath(args.deal)), SETTLED,
                                     os.environ.get("BUYER_MEMORY", "memory/market_memory_r3.json"))
    except Exception:  # noqa: BLE001
        line = None
    if line:
        print(f"[forum] buyer post queued: ledger #{line['refs'][0]}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
