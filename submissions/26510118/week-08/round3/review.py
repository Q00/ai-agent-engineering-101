"""Round 3 review hook (B4): the kit runs it after each settled deal and posts what it prints.

    python review.py --deal .market/deal.json
    {"stars": 4, "review": "Paid 16000 for ... (list 20000); grade B; ..."}

Facts only, written by code: in week 7 a model asked to word reviews wrote "the
receipt did not match" into deals that had no receipt. A false grade claim is
1 star; otherwise the stars follow the price against the list price. The deal also
goes into the buyer's purchase book and shop notebook (buyer_memory.py), where a
breakdown hours later is matched to it.
"""
import argparse
import time
from pathlib import Path

import buyer_memory
import common


def judge(deal: dict, catalog: dict) -> dict:
    rec, got = deal.get("receipt") or {}, deal.get("settled") or {}
    price = rec.get("price")
    sku = got.get("sku") or rec.get("item")
    lp = (catalog.get(sku) or {}).get("list_price")
    facts = [f"Paid {price} for {got.get('name') or sku}" + (f" (list {lp})" if lp else "")]
    if got.get("grade"):
        facts.append(f"grade {got['grade']}")
    if got.get("claim"):
        facts.append(f"the shop claimed {got['claim']}, which was " + ("true" if got.get("claim_honest") else "false"))
    if got.get("expires_at"):
        facts.append(f"{(got['expires_at'] - time.time()) / 3600:.0f} h of shelf life left")
    if got.get("warranty_hours"):
        facts.append(f"{got['warranty_hours']} h warranty")
    if got.get("claim_honest") is False:
        stars = 1
    elif lp and isinstance(price, (int, float)):
        r = price / lp
        stars = 5 if r <= 0.7 else 4 if r <= 0.85 else 3 if r <= 1.0 else 2
    else:
        stars = 3
    return {"stars": stars, "review": "; ".join(facts) + "."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=str(common.MARKET_DIR / "deal.json"))
    path = Path(ap.parse_args().deal)
    deal = common.read_json(path, {}) or {}
    out = judge(deal, common.catalog(path.parent))
    buyer_memory.on_settled(deal)
    common.record("reviews", order=(deal.get("order") or {}).get("order"), **out)
    common.last_line(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
