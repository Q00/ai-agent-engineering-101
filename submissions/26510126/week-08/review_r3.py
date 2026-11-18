"""Round 3 review hook: stars and a review from what the market says arrived, after each paid deal.

    python review_r3.py --deal .market/deal.json

deal.json holds the order, the receipt and the market's settled record (SKU, grade, the shop's claim and whether
it was true, shelf life, warranty, value). The stars are facts, not moods: a false grade claim is one star; food
that will not last the order's use_within_hours is two; otherwise five, four when we paid more than 85% of list.
The settled record goes to memory.jsonl as raw evidence (market_buyer's Round 3 path reads it back), together
with the kit's newest payment refusals and broken goods.
"""
import argparse
import json
import time

import buyer_r3
import market_buyer


def stars_for(order, receipt, s):
    lp = (buyer_r3.catalog().get(s.get("sku")) or {}).get("list_price") or s.get("value") or 0
    price = receipt.get("price") or 0
    name = s.get("name") or s.get("sku")
    share = f"{price / lp:.0%} of list {lp}" if lp else f"{price}"
    if s.get("claim_honest") is False:
        return 1, f"{name}: the shop claimed grade {s.get('claim')} but shipped grade {s.get('grade')} (ledger #{s.get('ledger')}). Paid {share}.", lp
    left = (s["expires_at"] - time.time()) / 3600 if s.get("expires_at") else None
    need = order.get("use_within_hours")
    if left is not None and need and left < need:
        return 2, f"{name}: {left:.1f} h of shelf life left, we needed {need} h. Paid {share}.", lp
    if s.get("value") == 0:
        return 2, f"{name}: worth nothing to this order by the market's record. Paid {share}.", lp
    bits = [f"grade {s.get('grade')}"]
    if s.get("claim"):
        bits.append(f"claimed {s['claim']}, true")
    if left is not None:
        bits.append(f"{left:.0f} h of shelf life left")
    if s.get("warranty_hours"):
        bits.append(f"{s['warranty_hours']} h warranty")
    stars = 4 if lp and price > 0.85 * lp else 5
    return stars, f"{name}: {', '.join(bits)}; paid {share}.", lp


def main(path):
    deal = buyer_r3.load(path, {})
    order, receipt, s = deal.get("order") or {}, deal.get("receipt") or {}, deal.get("settled") or {}
    memory = market_buyer.load_memory()
    stars, text, lp = stars_for(order, receipt, s)
    trade = next((m for m in reversed(memory) if m.get("order") == order.get("order") and m.get("kind") == "trade"), {})
    market_buyer.remember({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "settled", "round": 3, "order": order.get("order"),
        "shop": trade.get("shop") or str(s.get("shop", "")).rstrip("/").rsplit("/shop/", 1)[-1], "url": s.get("shop"),
        "sku": s.get("sku"), "name": s.get("name"), "unit": s.get("unit"), "price": receipt.get("price"),
        "list_price": lp, "grade": s.get("grade"), "claim": s.get("claim"), "claim_honest": s.get("claim_honest"),
        "expires_at": s.get("expires_at"), "warranty_hours": s.get("warranty_hours"), "value": s.get("value"),
        "ledger": s.get("ledger"), "settled_at": time.time(), "food_hours": order.get("use_within_hours"),
        "stars": stars})
    memory = market_buyer.load_memory()
    buyer_r3.sync_kit_files(memory, market_buyer.remember)
    print(json.dumps({"stars": stars, "review": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    main(ap.parse_args().deal)
