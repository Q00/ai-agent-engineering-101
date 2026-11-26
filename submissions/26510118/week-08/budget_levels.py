"""The budgets the market's order desk draws, read from the public ledger.

    python budget_levels.py --hours 6        (writes budget_levels.json)

Round 2 orders still carry their budget. Across 43,711 orders in twelve hours
the desk drew five values per item, each about 20% of the time (lamp 90, 105,
110, 125, 140; one in five below the reserve). Round 3 hides each order's
budget, not that set. The shop uses it in one rule (shop_core): an offer below
my ask that is exactly one of these values is most likely the buyer's whole
budget, so it is taken at once instead of haggled over for another round.
"""
from __future__ import annotations

import argparse
import collections
import json
import time
from pathlib import Path

import httpx

MARKET = "https://market.ourolabs.ai"
OUT = Path(__file__).resolve().parent / "budget_levels.json"
MIN_SHARE = 0.05                                        # a value drawn less often than this is noise, not a level


def page(c: httpx.Client, after: int) -> list:
    r = c.get(f"{MARKET}/api/ledger", params={"after": after})
    r.raise_for_status()
    return r.json()


def seq_before(c: httpx.Client, ts: float) -> int:
    """A ledger seq just before `ts`: gallop, then halve (about 40 requests)."""
    lo, hi = 0, 512
    while (p := page(c, hi)) and p[0]["ts"] < ts:
        lo, hi = hi, hi * 2
    while hi - lo > 500:
        mid = (lo + hi) // 2
        p = page(c, mid)
        if p and p[0]["ts"] < ts:
            lo = mid
        else:
            hi = mid
    return lo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=6)
    a = ap.parse_args()
    until = time.time()
    since = until - a.hours * 3600
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    hidden = 0
    with httpx.Client(timeout=30) as c:
        after = seq_before(c, since)
        while p := page(c, after):
            for e in p:
                if e["ts"] >= since and e["kind"] == "order":
                    d = e["data"]
                    if d.get("budget") is None:
                        hidden += 1                     # a Round 3 order: the budget is a commitment now
                    else:
                        counts[d.get("item")][d["budget"]] += 1
            after = p[-1]["seq"]
            time.sleep(0.1)                             # a public endpoint: read it gently
    levels = {item: sorted(b for b, n in cnt.items() if n / sum(cnt.values()) >= MIN_SHARE)
              for item, cnt in counts.items()}
    out = {"from": since, "to": until, "orders_with_budget": sum(sum(c.values()) for c in counts.values()),
           "orders_hidden": hidden, "levels": levels,
           "shares": {item: {str(b): round(n / sum(cnt.values()), 3) for b, n in sorted(cnt.items())}
                      for item, cnt in counts.items()}}
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    stamp = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
    print(f"{stamp(since)} to {stamp(until)}: {out['orders_with_budget']} orders with a budget, {hidden} hidden")
    for item, lv in levels.items():
        print(f"  {item:<9} " + ", ".join(f"{b} ({out['shares'][item][str(b)]:.0%})" for b in lv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
