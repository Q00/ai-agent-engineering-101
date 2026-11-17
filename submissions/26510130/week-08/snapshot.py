"""Save the market board, for challenges 1 and 2.

    python snapshot.py --label before        # one snapshot
    python snapshot.py --label before --every 300 --count 6

Writes logs/challenge-1/board-<label>-<HHMMSS>.json and prints this shop's row:
share, deals, average price, stars. The board is the market's own numbers, so
the before/after comparison in challenge 1 does not depend on my logs alone.
"""
import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime
from pathlib import Path

MARKET = "https://market.ourolabs.ai"
NAME = "YAHO1212"


def board(round_=2):
    with urllib.request.urlopen(f"{MARKET}/api/board?round={round_}", timeout=30) as r:
        return json.load(r)


def mine(d):
    shop = next((s for s in d.get("shops", []) if s.get("name") == NAME), None)
    buyer = next((b for b in d.get("buyers", []) if b.get("name") == NAME), None)
    revs = [r for r in d.get("reviews", []) if r.get("shop") == NAME]
    return shop, buyer, revs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--every", type=int, default=0)
    ap.add_argument("--count", type=int, default=1)
    a = ap.parse_args()
    out = Path("logs/challenge-1")
    out.mkdir(parents=True, exist_ok=True)
    for i in range(a.count):
        d = board()
        stamp = datetime.now().strftime("%H%M%S")
        (out / f"board-{a.label}-{stamp}.json").write_text(
            json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        shop, buyer, revs = mine(d)
        if shop:
            print(f"{stamp} [{a.label}] shop deals={shop.get('deals')} share={shop.get('share')} "
                  f"revenue={shop.get('revenue')} stars={shop.get('stars')} "
                  f"visits={shop.get('visits')} reviews={len(revs)}")
        else:
            print(f"{stamp} [{a.label}] shop {NAME} not on the board")
        if i + 1 < a.count:
            time.sleep(a.every)


if __name__ == "__main__":
    main()
