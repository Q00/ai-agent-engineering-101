"""Where do deals get stuck? Reads logs/market-kit.log (time-stamped kit lines) and market.db (shop_deals),
writes reports/throughput_YYYYMMDD.md.

    python tools/throughput.py

Log lines carry only HH:MM:SS; a day is added whenever the clock goes backwards. Counts per clock hour:
  shop   requests  "[seller] [price]" (one per negotiation the seller answered with a unit)
         settled   "[acp] settled" (my shop's sales)
  buyer  orders    "[order o-..."      attempts "task <uuid>" (an A2A negotiation opened)
         deals     " DEAL "            paid "[acp] paid"
Order time: from an "[order" line to the "[market] ledger #" result line of that order.
Harness wait: from a buyer "seller [..." line to the next buyer "harness:" line.
"""
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "logs", "market-kit.log")
START_DAY = datetime(2026, 11, 5)
TS = re.compile(r"^(\d\d):(\d\d):(\d\d) ")


def lines_with_time():
    day, prev = START_DAY, None
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TS.match(line)
            if not m:
                continue
            t = timedelta(hours=int(m[1]), minutes=int(m[2]), seconds=int(m[3]))
            if prev is not None and t < prev - timedelta(hours=1):
                day += timedelta(days=1)
            prev = t
            yield day + t, line


def main():
    hours = defaultdict(Counter)
    order_start, order_times, harness_waits = None, [], []
    last_seller = None
    for ts, line in lines_with_time():
        h = ts.replace(minute=0, second=0)
        if "[seller] [price]" in line:
            hours[h]["shop_requests"] += 1
        elif "[acp] settled" in line:
            hours[h]["shop_settled"] += 1
        elif re.search(r"\[order o-[0-9a-f]+\]", line):
            hours[h]["orders"] += 1
            order_start = ts
        elif re.search(r"\[buyer\] \S+ task [0-9a-f-]{36}", line):
            hours[h]["attempts"] += 1
        elif " DEAL " in line and "[buyer]" in line:
            hours[h]["deals"] += 1
        elif "[acp] paid" in line:
            hours[h]["paid"] += 1
        elif "[market] ledger #" in line and order_start:
            order_times.append((ts - order_start).total_seconds())
            order_start = None
        if "[buyer]" in line and " seller [" in line:
            last_seller = ts
        elif "[buyer]" in line and " harness: " in line and last_seller:
            harness_waits.append((ts - last_seller).total_seconds())
            last_seller = None

    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    shop_out = Counter(dict(db.execute(
        "SELECT CASE WHEN outcome='receipt' AND NOT EXISTS (SELECT 1 FROM settles s WHERE s.unit=d.unit AND s.ts>=d.ts-60) "
        "THEN 'receipt, not paid' WHEN outcome='open' THEN 'open (buyer left without a word)' ELSE outcome END, "
        "COUNT(*) FROM shop_deals d GROUP BY 1").fetchall()))
    buy_out = Counter({f"{o}: {r}": n for o, r, n in db.execute(
        "SELECT outcome, reason, COUNT(*) FROM buyer_deals GROUP BY outcome, reason ORDER BY 3 DESC")})

    rows = sorted(hours.items())
    full = [(h, c) for h, c in rows if c["orders"] or c["shop_requests"]][1:-1] or rows   # drop partial first/last hour
    n = max(len(full), 1)
    avg = {k: sum(c[k] for _, c in full) / n for k in ("orders", "attempts", "deals", "paid")}
    online = [c for _, c in full if c["shop_requests"]] or [Counter()]      # hours the shop was reachable
    for k in ("shop_requests", "shop_settled"):
        avg[k] = sum(c[k] for c in online) / len(online)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0
    ot, hw = mean(order_times), mean(harness_waits)
    per_order_harness = hw * (sum(1 for _ in harness_waits) / max(len(order_times), 1))

    cap = 3600 / max(ot, 1)
    shop_rate = avg["shop_settled"] / max(avg["shop_requests"], 1)
    pay_rate = avg["paid"] / max(avg["deals"], 1)
    verdict = (f"Bottleneck: conversion, not capacity. The buyer uses {avg['orders'] / cap:.0%} of its one-at-a-time "
               f"capacity ({avg['orders']:.0f} of about {cap:.0f} orders/h) but only {pay_rate:.0%} of its deals get paid; "
               f"the shop settles {shop_rate:.0%} of the {avg['shop_requests']:.0f} requests/h it gets while online.")

    out = [f"# Throughput diagnosis {datetime.now():%Y-%m-%d %H:%M}", "",
           f"Source: logs/market-kit.log ({rows[0][0]:%m-%d %H}:00 to {rows[-1][0]:%m-%d %H}:00), market.db. "
           f"Averages over {n} full hours (shop: only hours it was reachable).", "",
           "## Per hour", "",
           "| hour | shop requests | shop settled | buyer orders | negotiations | deals | paid |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {h:%m-%d %H}:00 | {c['shop_requests']} | {c['shop_settled']} | {c['orders']} | {c['attempts']} | "
            f"{c['deals']} | {c['paid']} |" for h, c in rows]
    out += ["", f"Average per full hour: shop requests {avg['shop_requests']:.1f}, settled {avg['shop_settled']:.1f}; "
            f"buyer orders {avg['orders']:.1f}, negotiations {avg['attempts']:.1f}, deals {avg['deals']:.1f}, "
            f"paid {avg['paid']:.1f}.", "",
            "## Shop: how negotiations ended (shop_deals, since 11/06 00:16)", ""]
    out += [f"- {k}: {v}" for k, v in shop_out.most_common()] or ["- no rows yet"]
    out += ["", "## Buyer: how negotiations ended (buyer_deals, since 11/06 00:14)", ""]
    out += [f"- {k}: {v}" for k, v in buy_out.most_common(8)] or ["- no rows yet"]
    out += ["", "## Buyer: time", "",
            f"- time per order (order line to result line): mean {ot:.0f} s, median {med(order_times):.0f} s, "
            f"n {len(order_times)}",
            f"- harness wait per call: mean {hw:.1f} s, median {med(harness_waits):.1f} s, n {len(harness_waits)}; "
            f"about {per_order_harness:.0f} s per order",
            "- orders handled at once: 1 (market_kit.py takes the next order only after handle_order returns; "
            "the kit is outside submissions/ and is not changed)", "",
            "## Conclusion", "", verdict, ""]
    path = os.path.join(HERE, "reports", f"throughput_{datetime.now():%Y%m%d}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n".join(out))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
