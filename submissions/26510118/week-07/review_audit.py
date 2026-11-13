"""How my buyer's reviews read on the public ledger over a time window: the
star spread, why the 3s are 3s, and how many no-deal reviews talk about a
receipt there never was. Run it before and after a change to the review rule.

    python review_audit.py --hours 24 > logs/review-audit/before.txt
    python review_audit.py --since 1793076000 > logs/review-audit/after.txt
"""
from __future__ import annotations

import argparse
import collections
import re
import time

import httpx

from buyer_core import FLOOR_TOLERANCE, FLOORS
from reprice import page, seq_before

BUYER = "Kiro729"
# a no-deal review has no receipt to match; these phrases claim one anyway
PHANTOM = re.compile(r"receipt (did not|didn't) match|not receive a receipt|no receipt match", re.I)


def results(since: float, until: float) -> list[dict]:
    rows, orders = [], {}
    with httpx.Client(timeout=30) as c:
        after = seq_before(c, since)
        while p := page(c, after):
            for e in p:
                if e["ts"] < since:
                    continue
                if e["ts"] > until:
                    return rows
                d = e["data"]
                if e["kind"] == "order" and d.get("buyer") == BUYER:
                    orders[d["order"]] = d
                elif e["kind"] == "result" and d.get("buyer") == BUYER:
                    o = orders.pop(d.get("order"), {})
                    rec = d.get("receipt") if isinstance(d.get("receipt"), dict) else {}
                    rows.append({"item": o.get("item") or rec.get("item"), "budget": o.get("budget"),
                                 "price": rec.get("price"), "stars": d.get("stars"),
                                 "review": d.get("review") or ""})
            after = p[-1]["seq"]
            time.sleep(0.2)                             # a public endpoint: read it gently
    return rows


def at_floor(r: dict) -> bool:
    floor = FLOORS.get(r["item"])
    return floor is not None and r["price"] <= int(floor * FLOOR_TOLERANCE)


def report(rows: list[dict], since: float, until: float) -> None:
    stamp = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
    deals = [r for r in rows if r["price"]]
    nodeal = [r for r in rows if not r["price"]]
    stars = collections.Counter(r["stars"] for r in rows)
    print(f"window {stamp(since)} to {stamp(until)}: {len(rows)} results by {BUYER}, "
          f"{len(deals)} deals, {len(nodeal)} no deals")
    print("stars, all results:", dict(sorted(stars.items(), key=lambda x: (x[0] is None, x[0] or 0))))
    for name, part in (("deals", deals), ("no deals", nodeal)):
        got = [r["stars"] for r in part if isinstance(r["stars"], int)]
        if got:
            c = collections.Counter(got)
            print(f"  {name:<9} avg {sum(got) / len(got):.2f}  " + "  ".join(f"{s}: {c[s]}" for s in range(1, 6)))
    threes = [r for r in rows if r["stars"] == 3]
    under = [r for r in threes if r["price"] and r["price"] < r["budget"]]
    print(f"why the {len(threes)} threes are threes:")
    print(f"  no deal                    {sum(1 for r in threes if not r['price'])}")
    print(f"  paid the full budget       {sum(1 for r in threes if r['price'] and r['price'] >= r['budget'])}")
    print(f"  bought under budget        {len(under)}  (at the market floor: {sum(1 for r in under if at_floor(r))})")
    print(f"no-deal reviews that mention a receipt: {sum(1 for r in nodeal if PHANTOM.search(r['review']))} "
          f"of {len(nodeal)}")
    for r in rows[-5:]:
        print(f"  e.g. [{r['stars']}] {r['item']} {r['budget']} -> {r['price']} | {r['review'][:110]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", type=float, help="epoch seconds; default: --hours before --until")
    ap.add_argument("--until", type=float, default=None, help="epoch seconds; default: now")
    ap.add_argument("--hours", type=float, default=24)
    a = ap.parse_args()
    until = a.until or time.time()
    since = a.since or until - a.hours * 3600
    report(results(since, until), since, until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
