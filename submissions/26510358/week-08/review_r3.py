"""Round 3 review hook (after each settled purchase): stores what the market says we got, then rates it by facts.

    python3 review_r3.py --deal {deal}

Stars: 1 if the item was worth nothing to the order (food that did not last, broken goods), 2 if the shop's grade
claim was false, 5 if honest and at most 80% of list price, else 4. The review states facts only.
"""
import argparse
import json

from r3lib import market
from r3lib.memory import Memory


def rate(deal):
    s, rec = deal.get("settled") or {}, deal.get("receipt") or {}
    info = market.catalog().get(s.get("sku"), {})
    L, price = info.get("list_price") or s.get("value") or 0, rec.get("price") or 0
    if s.get("value") == 0:
        stars = 1
    elif s.get("claim_honest") is False:
        stars = 2
    elif L and price <= 0.8 * L:
        stars = 5
    else:
        stars = 4
    bits = [f"{s.get('name') or info.get('name') or s.get('sku')} at {price} (list {L})", f"grade {s.get('grade')}"]
    if s.get("claim"):
        bits.append(f"claimed {s['claim']}, {'honest' if s.get('claim_honest') else 'NOT honest'}")
    if s.get("warranty_hours"):
        bits.append(f"{s['warranty_hours']} h warranty")
    if s.get("value") == 0:
        bits.append("worth nothing to the order")
    return stars, "; ".join(bits) + "."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    deal = json.loads(open(ap.parse_args().deal, encoding="utf-8").read())
    try:
        Memory().record_settled(deal)
    except Exception as e:
        print(json.dumps({"event": "memory_error", "error": str(e)[:200]}), flush=True)
    stars, review = rate(deal)
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
