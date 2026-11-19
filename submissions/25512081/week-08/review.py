"""Round 3 review hook: after each settled deal the kit runs this with deal.json and posts the stars it prints.

    python review.py --deal .market/deal.json

The review is written from the market's settlement record only (what was shipped, its grade, whether the shop's
grade claim was true, shelf life left, warranty), never from the shop's words. It also writes the purchase to
.market/purchases.json, the buyer's record of what it bought where: broken goods arrive later with only a name
and a time, and this record is how the buyer finds the shop that sold them (memory.py).
Prints {"stars": 1-5, "review": "..."} as its last line.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import memory


def stars_for(order, receipt, s):
    price = receipt.get("price") or 0
    lp = s.get("value") or 0
    name = (s.get("name") or receipt.get("item") or "")[:40]
    if s.get("claim") and s.get("claim_honest") is False:
        return 1, f"Grade claim was false: the shop claimed {s.get('claim')}, the market says {s.get('grade')}."
    if order.get("kind") == "food" and s.get("expires_at"):
        left = (s["expires_at"] - time.time()) / 3600
        need = order.get("use_within_hours") or 0
        if left < need:
            return 1, f"{name}: only {left:.0f}h of shelf life left at delivery, I needed {need}h."
    if not lp:
        return 2, f"{name}: settled at {price}, but the unit was worth nothing to my order."
    off = 1 - price / lp
    what = f"{name}, grade {s.get('grade')}" + (" (claim true)" if s.get("claim") else "")
    extra = f", {s['warranty_hours']}h warranty" if s.get("warranty_hours") else ""
    if off >= 0.10:
        return 5, f"{what}{extra}: paid {price}, {off:.0%} below list. Receipt and settlement matched."
    if off >= 0:
        return 4, f"{what}{extra}: paid {price}, close to list ({off:.0%} below). Settlement matched."
    return 3, f"{what}: paid {price}, above the list price {lp}."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=".market/deal.json")
    deal = json.loads(Path(ap.parse_args().deal).read_text(encoding="utf-8"))
    order, receipt, s = deal.get("order") or {}, deal.get("receipt") or {}, deal.get("settled") or {}
    stars, text = stars_for(order, receipt, s)
    memory.add_purchase({
        "order": order.get("order"), "shop_url": s.get("shop"), "shop": memory.shop_name(s.get("shop")),
        "sku": s.get("sku"), "name": s.get("name"), "kind": order.get("kind"), "price": receipt.get("price"),
        "value": s.get("value"), "grade": s.get("grade"), "claim_honest": s.get("claim_honest"),
        "warranty_hours": s.get("warranty_hours") or 0, "ledger": s.get("ledger"), "ts": int(time.time()),
        "stars": stars})
    print(json.dumps({"stars": stars, "review": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
