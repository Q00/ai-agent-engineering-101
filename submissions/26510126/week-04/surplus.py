"""Who did better out of the deals that closed, the buyer or the seller?

Every scenario where a deal is possible defines a bargaining zone, from the
seller's reserve to the buyer's budget. A deal at price p splits that zone:
the seller takes p - reserve and the buyer takes budget - p. Reporting the
seller's share of the zone puts every scenario on one axis however wide it
is, so s1's zone of 80 and s2's zone of 10 can be compared.

  0.5   an even split
  1.0   the seller took the whole zone; the buyer paid exactly its budget
  0.0   the buyer took the whole zone; the seller got exactly its reserve
  >1    the buyer paid above its budget
  <0    the seller sold below its reserve

The last two are the violations, and putting them on the same axis is the
point: a violation is not a separate kind of event, it is a split that ran
off the end of the zone.

One caveat carried over from section 4. The price analysed here is the price
the protocol layer recorded, which is not always the price the agents agreed
on. The three tagged deals on s2 are recorded at 85 because a counter-offer
of 95 arrived inside a reject-proposal and was never priced. The split for
those rows is the harness's view of the bargain, not the agents'.

  python surplus.py
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def scenarios():
    return {str(s["id"]): s for s in
            json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))}


def whose_price() -> dict:
    """Which side's proposal the accepting side took, read from the logs.

    The episode loop prices a deal from the other side's last proposal and
    says so in the log line, so the log already records who named the number
    that closed the negotiation.
    """
    out = {}
    for d in ("logs", "logs_ext"):
        for p in sorted((HERE / d).glob("*.log")):
            run = scenario = None
            for line in p.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^run=(\S+)", line)
                if m:
                    run = m.group(1)
                m = re.match(r"^\s*scenario (\S+):", line)
                if m:
                    scenario = m.group(1)
                m = re.search(r"DEAL at (\d+) \(last (buyer|seller) proposal\)", line)
                if m and run and scenario:
                    out[(run, scenario)] = m.group(2)
    return out


def rows():
    for name in ("results.csv", "results_ext.csv"):
        path = HERE / name
        if not path.is_file():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8", newline="")):
            if r.get("outcome") != "deal" or not (r.get("price") or "").strip():
                continue
            r["vocab"] = r.get("vocab") or "4"
            r["abstain"] = r.get("abstain") or "0"
            yield r


def label(r) -> str:
    if r["abstain"] == "1":
        return f"{r['condition']} (abstain)"
    return f"{r['condition']} ({r['vocab']} acts)"


def main() -> int:
    sc = scenarios()
    proposer = whose_price()
    deals = list(rows())
    if not deals:
        print("no priced deals found")
        return 1

    by = defaultdict(list)
    by_scenario = defaultdict(list)
    took = defaultdict(lambda: defaultdict(int))
    for r in deals:
        s = sc[r["scenario"]]
        zone = s["budget"] - s["reserve"]
        if zone <= 0:
            continue
        share = (int(r["price"]) - s["reserve"]) / zone
        key = label(r)
        by[key].append(share)
        by_scenario[(key, r["scenario"])].append(share)
        who = proposer.get((r["run"], r["scenario"]))
        if who:
            took[key][who] += 1
        r["_share"] = share

    print(f"# {len(deals)} closed deals\n")
    print("## Seller's share of the bargaining zone\n")
    print("| run | deals | mean seller share | who is ahead | "
          "the accepted price was named by |")
    print("|---|---|---|---|---|")
    for k in sorted(by):
        v = by[k]
        mean = sum(v) / len(v)
        ahead = ("seller" if mean > 0.55 else
                 "buyer" if mean < 0.45 else "about even")
        t = took[k]
        named = " / ".join(f"{w} {t[w]}" for w in ("buyer", "seller") if t[w]) or "-"
        print(f"| {k} | {len(v)} | {mean:+.2f} | {ahead} | {named} |")

    print("\n## By scenario\n")
    print("| run | scenario | zone | prices | seller share |")
    print("|---|---|---|---|---|")
    for (k, s_id), v in sorted(by_scenario.items()):
        s = sc[s_id]
        prices = sorted({int(r["price"]) for r in deals
                         if label(r) == k and r["scenario"] == s_id})
        print(f"| {k} | {s_id} | {s['reserve']}..{s['budget']} | "
              f"{', '.join(str(p) for p in prices)} | "
              f"{sum(v)/len(v):+.2f} |")

    print("\n## Deals that ran off the end of the zone\n")
    off = [r for r in deals if r.get("_share") is not None
           and (r["_share"] > 1 or r["_share"] < 0)]
    if not off:
        print("none")
    else:
        print("| run | scenario | price | zone | seller share | who overpaid or undersold |")
        print("|---|---|---|---|---|---|")
        for r in sorted(off, key=lambda r: (label(r), r["scenario"])):
            s = sc[r["scenario"]]
            side = ("buyer paid above budget" if r["_share"] > 1
                    else "seller sold below reserve")
            print(f"| {label(r)} | {r['scenario']} | {r['price']} | "
                  f"{s['reserve']}..{s['budget']} | {r['_share']:+.2f} | {side} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
