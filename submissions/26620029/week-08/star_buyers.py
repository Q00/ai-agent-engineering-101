"""Who rates my shop and how: stars per reviewing account, per hour, and the low-star reviews (the user's question 11-11 12:50).

The midterm ranks stars by one mean per reviewing account (Patch 3.4), so a few accounts that always give low stars weigh as
much as many that give five. Usage (inside market-kit, from week-08): python -X utf8 star_buyers.py HOURS NEWEST_SEQ
"""
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import monitor as m

KST = timezone(timedelta(hours=9))
PER_HOUR = 17_000


def main():
    hours, newest = float(sys.argv[1]), int(sys.argv[2])
    cut = time.time() - hours * 3600
    by_acct, by_hour, low = defaultdict(list), defaultdict(list), []
    cur = newest - int(hours * PER_HOUR * 1.1)
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
            if rc.get("shop") != m.ME or d.get("stars") is None:
                continue
            by_acct[d.get("buyer")].append(d["stars"])
            by_hour[datetime.fromtimestamp(e["ts"], KST).strftime("%m-%d %H")].append(d["stars"])
            if d["stars"] <= 3:
                low.append((d.get("buyer"), d["stars"], rc.get("item"), rc.get("claim"), (d.get("review") or "")[:90]))
        if len(page) < 500:
            break
    mean = lambda xs: sum(xs) / len(xs)
    print(f"my results with stars, {hours} h: {sum(map(len, by_acct.values()))}, accounts {len(by_acct)}")
    print("per hour (KST): results, mean stars")
    for h in sorted(by_hour):
        print(f"  {h}: {len(by_hour[h]):4d}, {mean(by_hour[h]):.2f}")
    accts = sorted(by_acct.items(), key=lambda kv: mean(kv[1]))
    print(f"mean of account means {mean([mean(v) for v in by_acct.values()]):.3f}, mean of all results "
          f"{mean([s for v in by_acct.values() for s in v]):.3f}")
    print("accounts, lowest mean first: account, results, mean, stars 1-5 counts")
    for a, v in accts:
        print(f"  {a}: {len(v)}, {mean(v):.2f}, {[v.count(i) for i in range(1, 6)]}")
    print(f"reviews at 3 stars or less: {len(low)}, last 25")
    for r in low[-25:]:
        print("  ", r)


if __name__ == "__main__":
    main()
