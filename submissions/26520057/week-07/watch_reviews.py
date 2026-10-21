"""Print every trade result at my shop as it lands in the market ledger: buyer, item, price, stars, review.

    python watch_reviews.py                          # deployed shop URL
    python watch_reviews.py --url <shop URL>         # after the URL changes

The ledger names the shop by the URL the buyer used, so results are matched on that URL.
"""
import argparse
import time

import httpx

MARKET = "https://market.ourolabs.ai"

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="https://cheapest-in-town-26520057.cheapest-in-town-worker.workers.dev")
ap.add_argument("--every", type=int, default=20, help="seconds between polls")
args = ap.parse_args()

after = -500  # start with the last 500 ledger entries
while True:
    try:
        d = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=20).json()
        entries = d if isinstance(d, list) else d.get("entries", [])
        for e in entries:
            after = max(after, e["seq"])
            x = e.get("data", {})
            if e["kind"] != "result" or args.url.rstrip("/") not in str(x.get("shop", "")):
                continue
            r = x.get("receipt") or {}
            deal = f"{r.get('item')} at {r.get('price')}" if r else "no deal"
            print(f"#{e['seq']} R{e.get('round')} {x.get('buyer')}: {deal}, stars {x.get('stars')}, "
                  f"verified {x.get('verified')} | {x.get('review') or ''}", flush=True)
    except Exception as ex:
        print(f"[poll failed] {ex!r}", flush=True)
    time.sleep(args.every)
