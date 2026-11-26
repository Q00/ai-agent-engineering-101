"""A dated picture of the shop and the buyer, for the before/after of Round 3.

    python snapshot.py --label round2-baseline
    python snapshot.py --label round3 --since <epoch>     # journal from that time on

What the shop asked first and what it sold at come from its own negotiation
journal (GET /shop/journal on the Worker): the ledger stops showing budgets in
Round 3, but the shop still sees every ask and offer it made. The rest is the
market's public side -- /api/stats (prices per round), /api/board (the
dashboard's rows for this shop and buyer) -- and the Worker's own counts
(/shop/log settlements, /buyer/status orders). Everything lands in
logs/snapshots/<label>.json, journal included, so the numbers can be redone.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import time
from pathlib import Path

import httpx

WORKER = "https://kiro-shop.kiro729.workers.dev"
MARKET = "https://market.ourolabs.ai"
NAME = "Kiro729"
RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
OUT = Path(__file__).resolve().parent / "logs" / "snapshots"


def per_item(eps: list) -> dict:
    """First ask, how buyers met it, and what sold, per item."""
    out = {}
    for item, reserve in RESERVES.items():
        e = [x for x in eps if x["item"] == item and x["offers"] and x["asks"]]
        if not e:
            continue
        feas = [x for x in e if max(x["offers"]) >= reserve]   # buyers who could reach the reserve
        deals = [x for x in e if x["outcome"] == "deal" and x.get("price")]
        out[item] = {
            "negotiations": len(e),
            "first_asks": dict(collections.Counter(str(x["asks"][0]) for x in e).most_common()),
            "reached_reserve": len(feas),
            "accepted_first_ask": sum(1 for x in feas if x["offers"][0] >= x["asks"][0]),
            "deals": len(deals),
            "deal_price_mean": round(statistics.fmean(x["price"] for x in deals), 1) if deals else None,
            "deal_price_median": statistics.median(x["price"] for x in deals) if deals else None,
            "margin_mean": round(statistics.fmean(x["price"] - reserve for x in deals), 1) if deals else None,
            "outcomes": dict(collections.Counter(x["outcome"] for x in e)),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True)
    ap.add_argument("--since", type=float, default=0, help="journal episodes from this epoch on")
    a = ap.parse_args()
    c = httpx.Client(timeout=60)
    journal = c.get(f"{WORKER}/shop/journal", params={"since": a.since}).json()
    stats = c.get(f"{MARKET}/api/stats").json()
    board = c.get(f"{MARKET}/api/board").json()
    snap = {
        "label": a.label, "at": time.time(), "since": a.since, "market_round": stats.get("round"),
        "shop": per_item(journal["episodes"]),
        "market_items": {r: v.get("items") for r, v in (stats.get("rounds") or {}).items()},
        "board": {role: next(({k: v for k, v in x.items() if k not in ("owner", "card")}
                              for x in board.get(role, []) if x.get("name") == NAME), None)
                  for role in ("shops", "buyers")},
        "shop_settles": {k: v for k, v in c.get(f"{WORKER}/shop/log", params={"n": 1}).json().items() if k != "recent"},
        "buyer_status": {k: v for k, v in c.get(f"{WORKER}/buyer/status").json().items() if k != "recent"},
        "journal": journal["episodes"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{a.label}.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")

    stamp = time.strftime("%m-%d %H:%M", time.localtime(snap["at"]))
    print(f"{a.label} at {stamp}, market round {snap['market_round']}, {len(journal['episodes'])} journal episodes")
    for item, s in snap["shop"].items():
        top = next(iter(s["first_asks"]))
        print(f"  {item:<9} first ask {top} ({s['first_asks'][top]}/{s['negotiations']}), "
              f"{s['deals']} deals at mean {s['deal_price_mean']} (+{s['margin_mean']} over the reserve), "
              f"{s['accepted_first_ask']}/{s['reached_reserve']} took the first ask")
    for r, items in sorted(snap["market_items"].items()):
        if items:
            print(f"  market round {r}: " + ", ".join(f"{i} mean {v.get('mean')}" for i, v in items.items()))
    sh, bu = snap["board"]["shops"] or {}, snap["board"]["buyers"] or {}
    print(f"  dashboard shop: {sh.get('deals')} deals, {sh.get('stars')} stars, profit {sh.get('profit')}; "
          f"buyer: {bu.get('deals')} deals of {bu.get('orders')} orders")
    print(f"  settlements: shop {snap['shop_settles'].get('settled')} (refused {snap['shop_settles'].get('refused')}), "
          f"buyer {snap['buyer_status'].get('settled')} of {snap['buyer_status'].get('round3_orders')} Round 3 orders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
