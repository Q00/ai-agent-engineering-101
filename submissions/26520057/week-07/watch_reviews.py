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
ap.add_argument("--url", help="Explicit URL; otherwise follow the current market card")
ap.add_argument("--every", type=int, default=20, help="seconds between polls")
args = ap.parse_args()

after = -500  # start with the last 500 ledger entries
while True:
    try:
        url = args.url
        if not url:
            board = httpx.get(f"{MARKET}/api/board", timeout=15).json()
            shop = next(s for s in board["shops"] if s["name"] == "cheapest-in-town"
                        and (s.get("owner") or {}).get("login") == "danayoou")
            url = shop["card"]["url"]
        d = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=20).json()
        entries = d if isinstance(d, list) else d.get("entries", [])
        for e in entries:
            after = max(after, e["seq"])
            x = e.get("data", {})
            if e["kind"] != "result" or url.rstrip("/") not in str(x.get("shop", "")):
                continue
            r = x.get("receipt") or {}
            deal = f"{r.get('item')} at {r.get('price')}" if r else "no deal"
            print(f"#{e['seq']} R{e.get('round')} {x.get('buyer')}: {deal}, stars {x.get('stars')}, "
                  f"verified {x.get('verified')} | {x.get('review') or ''}", flush=True)
    except Exception as ex:
        print(f"[poll failed] {ex!r}", flush=True)
    time.sleep(args.every)
