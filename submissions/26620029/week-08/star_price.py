"""Stars against paid/list on my deals and the market's, from the ledger's result entries (plan 2 step 2).

Usage (inside market-kit, from week-08): python -X utf8 star_price.py [hours]
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import monitor as m

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6
PER_HOUR = 17_000                      # ledger entries an hour, about (11-11 01:22 to 02:22: 16,612)


def main():
    cat = json.loads((Path(".market") / "catalog.json").read_text(encoding="utf-8"))
    lp = lambda s: (cat.get(s) or {}).get("list_price")
    after = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if after is None:
        raise SystemExit("give the newest ledger seq as the second argument")
    start = after - int(HOURS * PER_HOUR)
    cut = time.time() - HOURS * 3600
    rows = {"me": defaultdict(list), "market": defaultdict(list)}
    cur = start
    while True:
        page = m.page(cur)
        if not page:
            break
        for e in page:
            cur = e["seq"]
            if e["kind"] != "result" or e["ts"] < cut:
                continue
            d = e["data"]
            rc = d.get("receipt") or {}
            st, price, lst = d.get("stars"), rc.get("price"), lp(rc.get("item"))
            if st is None or not price or not lst:
                continue
            who = "me" if rc.get("shop") == m.ME else "market"
            rows[who][(rc.get("claim") or "?") + " " + str((cat.get(rc.get("item")) or {}).get("kind"))].append((price / lst, st))
        if len(page) < 500:
            break
    bins = [0, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 9]
    for who in ("me", "market"):
        for g in sorted(rows[who]):
            r = rows[who][g]
            print(f"{who} grade {g}: {len(r)} results, mean stars {sum(s for _, s in r) / len(r):.2f}, "
                  f"mean paid/list {sum(p for p, _ in r) / len(r):.3f}")
            for lo, hi in zip(bins, bins[1:]):
                b = [s for p, s in r if lo <= p < hi]
                if len(b) >= 5:
                    print(f"   paid/list {lo:.2f}-{hi:.2f}: {len(b):5d}, stars {sum(b) / len(b):.2f}, "
                          f"5* {sum(1 for s in b if s == 5) / len(b):.0%}, <=3* {sum(1 for s in b if s <= 3) / len(b):.0%}")


if __name__ == "__main__":
    main()
