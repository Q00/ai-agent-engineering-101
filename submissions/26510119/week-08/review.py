"""The review hook: after each settled purchase, stars from what the market's receipt shows. No LLM.

    python review.py --deal <deal.json>          (the kit runs it; prints {"stars", "review"})

deal.json's `settled` part is the market's own receipt: the grade the market gave the unit, the shop's claim
and whether it was honest, the value to the order (0 if food did not last), and the ledger number. The stars
replace market_buyer's guess, and the settlement is added to buyer_memory.jsonl so the forum writer can cite it.
"""
import argparse
import json
import os
import time
from pathlib import Path

MARKET_DIR = Path(os.environ.get("MARKET_DIR", Path(__file__).with_name(".market")))
BUYER_MEMORY = Path(os.environ.get("BUYER_MEMORY") or Path(__file__).with_name("buyer_memory.jsonl"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=str(MARKET_DIR / "deal.json"))
    deal = json.loads(Path(ap.parse_args().deal).read_text(encoding="utf-8"))
    s = deal.get("settled") or {}
    receipt = deal.get("receipt") or {}
    price = receipt.get("price")
    value = s.get("value")
    name = s.get("name") or s.get("sku")
    if s.get("claim_honest") is False:
        stars, text = 1, f"Claimed grade {s.get('claim')} but the market graded {name} {s.get('grade')}."
    elif value == 0:
        stars, text = 2, f"{name} did not last as long as the order needed."
    elif value and price and price <= 0.7 * value:
        stars, text = 5, f"{name}, grade {s.get('grade')}, as described, at {price / value:.0%} of list price."
    else:
        stars, text = 4, f"{name}, grade {s.get('grade')}, as described" + (f", at {price}." if price else ".")
    shop = s.get("shop")
    try:
        import httpx
        shops = httpx.get("https://market.ourolabs.ai/api/shops", timeout=10).json()["shops"]
        shop = next((x["name"] for x in shops if x["url"].rstrip("/") == str(shop).rstrip("/")), shop)
    except Exception:
        pass
    with open(BUYER_MEMORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "settled", "ts": int(time.time()), "order": deal.get("order"), "shop": shop,
                            "sku": s.get("sku"), "name": name, "grade": s.get("grade"), "claim": s.get("claim"),
                            "claim_honest": s.get("claim_honest"), "value": value, "price": price,
                            "ledger": s.get("ledger"), "stars": stars}, ensure_ascii=False) + "\n")
    print(json.dumps({"stars": stars, "review": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
