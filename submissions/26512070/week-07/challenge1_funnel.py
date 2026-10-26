"""Challenge 1, the analysis behind a price change: where my shop loses buyers.

    py challenge1_funnel.py > logs/challenge-1/funnel-<time>.txt

Since the last entry in logs/challenge-1/changes.jsonl it prints:
  1. the funnel: market deals, my visits, negotiations recorded at my shop, deals (board deltas);
  2. every negotiation at my shop with its turns (trades/seller.jsonl);
  3. the median deal price per item that each shop's reviews mention (/api/shops), top shops and mine;
  4. the opening asks other shops gave my buyer (trades/buyer.jsonl) against my list prices;
  5. my shop's reply time per buyer message (logs/market-kit.log).
"""
import collections
import json
import os
import re
import statistics as st

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET = "https://market.ourolabs.ai"
NICK = "김관우"


def jsonl(path):
    return [json.loads(l) for l in open(os.path.join(HERE, path), encoding="utf-8") if l.strip()]


def main():
    last = jsonl("logs/challenge-1/changes.jsonl")[-1]
    since, s0 = last["time"].replace("T", " "), last["snapshot"]
    b = httpx.get(f"{MARKET}/api/board", timeout=30).json()
    me = next(s for s in b["shops"] if s["name"] == NICK)
    win = [r for r in jsonl("trades/seller.jsonl") if r["time"] > since]
    print(f"== 1. funnel since {since}")
    print(f"  market deals +{b['totals']['deals'] - s0['market_deals']}")
    print(f"  my visits {s0['me']['visits']} -> {me['visits']} (+{me['visits'] - s0['me']['visits']})")
    print(f"  negotiations recorded at my shop {len(win)}")
    print(f"  my counted deals {s0['me']['deals']} -> {me['deals']} (+{me['deals'] - s0['me']['deals']})")

    print("\n== 2. negotiations at my shop (b = buyer, s = seller)")
    for r in win:
        turns = [(t["from"][0], t["price"]) for t in r["turns"]]
        print(f"  {r['time'][11:16]} {r['item']:9} {r['outcome']:9} {str(r['final_price']):5} {turns}")
    print("  outcomes:", dict(collections.Counter(r["outcome"] for r in win)))

    share = {s["name"]: s.get("share") or 0 for s in b["shops"]}
    pat = re.compile(r"(lamp|chair|bookshelf)\D{0,30}?(\d{2,3})(?:\.0)?\b", re.I)
    rows = []
    for s in httpx.get(f"{MARKET}/api/shops", timeout=30).json()["shops"]:
        p = collections.defaultdict(list)
        for rv in s.get("reviews") or []:
            for m in pat.finditer(rv["review"]):
                p[m.group(1).lower()].append(int(m.group(2)))
        rows.append((share.get(s["name"], 0), s["name"], {k: st.median(v) for k, v in p.items()}))
    rows.sort(key=lambda x: -x[0])
    print("\n== 3. deal prices in each shop's reviews (median), by share")
    for sh, n, p in rows[:10] + [r for r in rows if r[1] == NICK]:
        print(f"  {n[:18]:18} share {sh:.3f}  lamp {p.get('lamp', '-')}  chair {p.get('chair', '-')}  "
              f"bookshelf {p.get('bookshelf', '-')}")

    inv = json.load(open(os.path.join(HERE, "inventory.json"), encoding="utf-8"))
    opens = collections.defaultdict(list)
    for r in jsonl("trades/buyer.jsonl"):
        asks = [t["price"] for t in r.get("turns", []) if t["from"] == "seller" and t.get("price") is not None]
        cp = r.get("counterpart") or ""
        if asks and cp and "Kit Shop" not in cp and "replay" not in cp:
            opens[r["item"]].append(asks[0])
    print("\n== 4. opening asks other shops gave my buyer (median, count) vs my list prices")
    for item in inv:
        v = opens.get(item, [])
        print(f"  {item:9} others {st.median(v) if v else '-'} ({len(v)})  mine {inv[item]['list_price']}")

    secs, start = [], None
    for line in open(os.path.join(HERE, "logs", "market-kit.log"), encoding="utf-8", errors="ignore"):
        m = re.match(r"(\d\d):(\d\d):(\d\d) \[seller\] (=== task|decision:)", line)
        if not m or line[:8] < since[11:19]:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if m.group(4) == "=== task":
            start = t
        elif start is not None:
            secs.append(t - start)
            start = None
    secs.sort()
    if secs:
        print(f"\n== 5. my reply time per buyer message: n={len(secs)}, median {secs[len(secs) // 2]} s, "
              f"p90 {secs[int(len(secs) * 0.9)]} s, max {secs[-1]} s")


if __name__ == "__main__":
    main()
