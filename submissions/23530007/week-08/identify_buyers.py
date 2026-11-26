"""Which buyer wrote which first message to our shop, and can a buyer be recognised from it?

    python identify_buyers.py [--pages 40] [--who totato,shaun]

The seller logs each buyer's first message with its A2A task id (seller_r3_log.jsonl, event "ask"), and the
public ledger names the buyer of each paid task (kind "result": buyer, receipt.task, stars). Joining the two
gives, per buyer, the shape of its first message with SKUs, product names and numbers taken out. A shape that
only one buyer uses can go into blocked_buyers.json as a regex. Prints, for the buyers asked about, each shape
with how many of its uses in our log came from that buyer (paid tasks only), and the stars that buyer gave us.
"""
import argparse
import json
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "seller_r3_log.jsonl"
MARKET = "https://market.ourolabs.ai"


def shape(text):
    t = re.sub(r"\(G[0-9a-f]{6,10}\)|\bG[0-9a-f]{6,10}\b", "<SKU>", text)
    t = re.sub(r"buy .*?(<SKU>)", r"buy <NAME> \1", t)
    return re.sub(r"\d[\d,.]*", "N", t)[:160]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=40)
    ap.add_argument("--who", default="totato,shaun")
    a = ap.parse_args()
    asks = {}
    for line in open(LOG, encoding="utf-8"):
        if '"event": "ask"' in line:
            e = json.loads(line)
            asks[e["task"]] = e.get("text", "")
    buyer, stars = {}, defaultdict(Counter)
    after = -500 * a.pages
    for _ in range(a.pages):
        with urllib.request.urlopen(f"{MARKET}/api/ledger?after={after}", timeout=30) as r:
            page = json.load(r)
        if not page:
            break
        for e in page:
            d = e.get("data") or {}
            task = (d.get("receipt") or {}).get("task")
            if e.get("kind") == "result" and task in asks:
                buyer[task] = d.get("buyer")
                stars[d.get("buyer")][d.get("stars")] += 1
        after = page[-1]["seq"]
        if len(page) < 500:
            break
    by_shape = defaultdict(Counter)
    for task, text in asks.items():
        by_shape[shape(text)][buyer.get(task, "?")] += 1
    print(f"{len(asks)} first messages logged, {len(buyer)} matched to a buyer on the ledger")
    for who in a.who.split(","):
        names = sorted({b for b in buyer.values() if b and b.startswith(who)})
        for n in names:
            print(f"\n== {n}: stars given to us {dict(stars[n])}")
            mine = Counter({s: c[n] for s, c in by_shape.items() if c[n]})
            for s, k in mine.most_common(8):
                known = sum(v for b, v in by_shape[s].items() if b != "?")
                print(f"  {k:3} of {known:3} paid uses of this shape | {s}")


if __name__ == "__main__":
    main()
