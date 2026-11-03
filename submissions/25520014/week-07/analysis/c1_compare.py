"""Challenge 1: the shop's share and average price before and after the pricing desk.

    python analysis/c1_compare.py [hours]     # window on each side of the deploy, default: as long as the after side

The desk went live when the kit registered at 10-22 05:00:50 UTC (commit 8d8c981). Before it the shop
opened at the cheapest verified sale by other shops. Prints, per window: visits, verified deals, deal
rate, share of all verified deals, average price per item and over the reserve; then the after side by hour.
"""
import collections
import datetime as dt
import sys

from ledger import ME, RESERVE, load, shop_names, shop_of, utc

DEPLOY = dt.datetime(2026, 10, 22, 5, 0, 50, tzinfo=dt.UTC).timestamp()

entries = load()
names = shop_names(entries)
now = entries[-1]["ts"]
span = min(float(sys.argv[1]) * 3600, now - DEPLOY) if len(sys.argv) > 1 else now - DEPLOY
results = [e for e in entries if e["kind"] == "result" and e["round"] == 2]


def stats(a, b):
    res = [e for e in results if a <= e["ts"] < b]
    verified = [e for e in res if e["data"].get("verified")]
    visits = [e for e in res if shop_of(e, names) == ME]
    deals = [e for e in verified if shop_of(e, names) == ME]
    prices = collections.defaultdict(list)
    for e in deals:
        rc = e["data"]["receipt"]
        prices[rc["item"]].append(round(float(rc["price"])))
    over = [p - RESERVE[i] for i, ps in prices.items() for p in ps]
    avg = lambda xs: f"{sum(xs) / len(xs):.1f} (n={len(xs)})" if xs else "-"
    return (f"visits {len(visits)}, verified deals {len(deals)} "
            f"(deal rate {len(deals) / len(visits) * 100 if visits else 0:.0f}%), "
            f"share {len(deals) / len(verified) * 100 if verified else 0:.2f}% of {len(verified)}; "
            f"avg lamp {avg(prices['lamp'])}, chair {avg(prices['chair'])}, bookshelf {avg(prices['bookshelf'])}; "
            f"over reserve {avg(over)}, above reserve {sum(o > 0 for o in over)}/{len(over)}")


print(f"ledger up to seq {entries[-1]['seq']} ({utc(now)} UTC); deploy {utc(DEPLOY)} UTC; "
      f"{span / 3600:.1f} h on each side\n")
print(f"before {utc(DEPLOY - span)}..{utc(DEPLOY)}: {stats(DEPLOY - span, DEPLOY)}")
print(f"after  {utc(DEPLOY)}..{utc(DEPLOY + span)}: {stats(DEPLOY, DEPLOY + span)}\n")
print("after the deploy, by hour:")
t = DEPLOY
while t < now:
    print(f"  {utc(t)}  {stats(t, t + 3600)}")
    t += 3600
