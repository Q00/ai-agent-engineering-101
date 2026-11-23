"""Week 08 -- the review hook: stars from what was delivered, not from the chat.

    python review_r3.py --deal .market/deal.json      # the kit runs it after each paid deal

The settled record says what the unit really was: its grade, whether the
shop's claim was honest, its shelf life, the warranty. The review is written
in code from those fields, so every sentence in it is checkable against the
ledger entry it cites -- a review is a claim other buyers act on.
"""
import argparse
import json
import time
from pathlib import Path

import buyer_r3


def judge(d):
    o, rec, s = d.get("order") or {}, d.get("receipt") or {}, d.get("settled") or {}
    price = rec.get("price") or 0
    name = (s.get("name") or rec.get("item") or "the item")[:40]
    lp = s.get("value") or (buyer_r3.mm.catalog().get(s.get("sku") or rec.get("item")) or {}).get("list_price") or price
    ref = f" (ledger #{s.get('ledger')})" if s.get("ledger") else ""
    if s.get("claim_honest") is False:
        return 1, f"{name}: the shop claimed grade {s.get('claim')}, the market says {s.get('grade')}{ref}.", "dishonest"
    if s.get("expires_at") and o.get("use_within_hours"):
        left = (s["expires_at"] - time.time()) / 3600
        if left < o["use_within_hours"]:
            return 1, f"{name}: {left:.0f} h of shelf life left, I needed {o['use_within_hours']} h{ref}.", "spoiled"
    gain = (lp - price) / lp if lp else 0
    stars = 5 if gain >= 0.2 else 4 if gain >= 0.05 else 3
    extra = f", grade {s.get('grade')}" + (f", {s['warranty_hours']} h warranty" if s.get("warranty_hours") else "")
    return stars, f"{name} at {price} (list {lp}){extra}; claim honest{ref}.", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    d = json.loads(Path(ap.parse_args().deal).read_text(encoding="utf-8"))
    stars, review, bad = judge(d)
    if bad:                                                   # remember it against the shop
        m = buyer_r3.load_mem()
        order = (d.get("order") or {}).get("order")
        p = next((p for p in m["purchases"] if p.get("order") == order), None)
        if p:
            buyer_r3.shop_row(m, p["shop"])[bad] += 1
            p[bad] = True
            buyer_r3.save_mem(m)
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
