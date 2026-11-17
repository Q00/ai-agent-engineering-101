"""Week 07 — shop_stats.py: one shop's numbers from the market's public API, for challenge 1.

    python shop_stats.py dragonash                         # whole round so far
    python shop_stats.py dragonash --since 2026-10-20T20:15 # only trades after a local time (e.g. a price change)
    python shop_stats.py dragonash --until 2026-10-20T20:15

Reads /api/stats (share, visits, receipts for the round) and /api/trades (each result: shop, item,
price, verified). Prints deals, share of counted deals in the window, average price per item for
this shop next to the market's average, and how many of its receipts verified.
"""
import argparse
import time
from datetime import datetime

import httpx

MARKET = "https://market.ourolabs.ai"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("shop")
    p.add_argument("--since")
    p.add_argument("--until")
    a = p.parse_args()
    since = datetime.fromisoformat(a.since).timestamp() if a.since else 0
    until = datetime.fromisoformat(a.until).timestamp() if a.until else time.time() + 1

    stats = httpx.get(MARKET + "/api/stats", timeout=20).json()
    rnd = stats["round"]
    trades = [t for t in httpx.get(MARKET + "/api/trades", timeout=20).json()
              if t["round"] == rnd and since <= t["ts"] < until and t.get("counted") and t.get("price") is not None]
    mine = [t for t in trades if t["shop"] == a.shop]
    row = next((s for s in stats["rounds"][str(rnd)]["shops"] if s["shop"] == a.shop), {})

    window = f"{datetime.fromtimestamp(since):%H:%M} - {datetime.fromtimestamp(min(until, time.time())):%H:%M}" if a.since or a.until else "whole round"
    print(f"{a.shop}, round {rnd}, {window} (now {datetime.now():%Y-%m-%d %H:%M:%S})")
    print(f"  round totals (dashboard): deals {row.get('deals')}, share {row.get('share')}, visits {row.get('visits')}, "
          f"receipts {row.get('receipts')}, with order {row.get('receipts_with_order')}")
    share = len(mine) / len(trades) if trades else 0
    print(f"  in window: {len(mine)} of {len(trades)} counted deals, share {share:.1%}, "
          f"verified receipts {sum(t['verified'] for t in mine)}/{len(mine)}")
    for item in ("lamp", "chair", "bookshelf"):
        m = [t["price"] for t in mine if t["item"] == item]
        allp = [t["price"] for t in trades if t["item"] == item]
        avg = lambda xs: f"{sum(xs) / len(xs):.1f}" if xs else "-"
        print(f"  {item:9} mine n={len(m):3} avg {avg(m):>6}   market n={len(allp):3} avg {avg(allp):>6}")


if __name__ == "__main__":
    main()
