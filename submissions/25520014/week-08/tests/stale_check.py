"""Did the 11-04 16:50 stale-stock rules work? Read-only, run on the server from the week-08 folder:

    python3 tests/stale_check.py [--since EPOCH]     # default: the deploy, 11-04 16:49 KST

Rules judged (clearance.py STALE_H, seller.py MENTION_H, restock.py OLD_C_H):
  - goods held 6 h+ open at the market's median less 5% (grade C less c_discount too), floor 0.9 x that ask;
  - a lower goods grade held 3 h+ and 5% cheaper is mentioned on the first reply; the buyer who asks gets it.
Prints, against the baseline at the deploy (goods held 6 h+: 43 units, 332만 at cost; goods C held 3 h+: 38 units,
319만 at 16:20):
  1. goods stock held 6 h+ and goods C held 3 h+ now, by grade
  2. talks since: clearance and mentioned ones, deals, what the deals paid against cost (a loss is under cost)
  3. mentioned talks: how many buyers took the lower grade, and the deal rate of mentioned talks against talks of
     the same SKUs with nothing mentioned (does the mention cost deals of the best grade?)
Verdict lines the owner set: stale goods at cost under the baseline while sales go on, clearance deals not losing
money on the whole, and the mentioned talks' deal rate not under the others'.
"""
import argparse
import json
import sqlite3
import time
from collections import Counter, defaultdict

DEPLOY = 1793778543          # 11-04 16:49:03 KST, the kit registered with the new seller
BASE = {"stale6": (43, 3_320_000), "oldc3": (38, 3_190_000)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, default=DEPLOY)
    a = ap.parse_args()
    t = time.time()
    db = sqlite3.connect("file:data/market.db?mode=ro", uri=True)
    names = dict(db.execute("select url,name from shops"))
    kind = dict(db.execute("select sku,kind from catalog"))
    inv = json.load(open(".market/inventory.json"))
    goods = [u for u in inv["units"] if kind.get(u["sku"]) != "food" and u.get("grade") != "expired"]
    s6 = [u for u in goods if t - u["supplied_at"] >= 6 * 3600]
    c3 = [u for u in goods if u["grade"] == "C" and t - u["supplied_at"] >= 3 * 3600]
    print(f"1. now ({(t - a.since) / 3600:.1f} h after): goods held 6 h+ {len(s6)} units {sum(u['cost'] for u in s6)/1e4:.0f}만 "
          f"(baseline {BASE['stale6'][0]}, {BASE['stale6'][1]/1e4:.0f}만) by grade {dict(Counter(u['grade'] for u in s6))}; "
          f"goods C held 3 h+ {len(c3)} units {sum(u['cost'] for u in c3)/1e4:.0f}만 (baseline {BASE['oldc3'][0]}, "
          f"{BASE['oldc3'][1]/1e4:.0f}만)")
    paid = {}
    for (d,) in db.execute("select data from ledger where round=3 and kind='settle' and ts>=?", (a.since,)):
        d = json.loads(d)
        if names.get(d["shop"]) == "inextro":
            paid[d["unit"]] = d["amount"]
    opens, ends = {}, {}
    for line in open("data/negotiations.jsonl"):
        r = json.loads(line)
        if r["ts"] < a.since:
            continue
        (opens if r["outcome"] == "open" else ends)[r["task"]] = r
    c = Counter()
    money = defaultdict(lambda: [0, 0.0, 0.0, 0])    # deals paid, paid, cost, under cost
    per_sku = defaultdict(lambda: Counter())
    for task, o in opens.items():
        if kind.get(o["sku"]) == "food":
            continue
        e = ends.get(task, {})
        deal = e.get("outcome") == "deal"
        tags = (["clearance"] if e.get("arm") == "clearance" or o.get("arm") == "clearance" else []) + \
               (["mentioned"] if o.get("mentioned") else [])
        for tag in tags or ["other"]:
            c[(tag, "talks")] += 1
            c[(tag, "deals")] += deal
            if deal and e.get("unit") in paid:
                m = money[tag]
                m[0] += 1; m[1] += paid[e["unit"]]; m[2] += e.get("cost", 0); m[3] += paid[e["unit"]] < e.get("cost", 0)
        if o.get("mentioned"):
            c[("mentioned", "took lower")] += deal and e.get("grade") == o["mentioned"]
            per_sku[o["sku"]]["m_talks"] += 1; per_sku[o["sku"]]["m_deals"] += deal
        elif o.get("chose") in ("A", "B"):
            per_sku[o["sku"]]["o_talks"] += 1; per_sku[o["sku"]]["o_deals"] += deal
    print("2. goods talks since the deploy (deals paid: paid / cost, deals under cost)")
    for tag in ("clearance", "mentioned", "other"):
        n, d = c[(tag, "talks")], c[(tag, "deals")]
        m = money[tag]
        print(f"   {tag:9} talks {n:4} deals {d:4} ({d / max(n, 1):.0%}) | paid {m[0]:4}: {m[1]/1e4:7.0f}만 / cost "
              f"{m[2]/1e4:7.0f}만, margin {(m[1]-m[2])/1e4:6.0f}만, under cost {m[3]}")
    both = [k for k, v in per_sku.items() if v["m_talks"] and v["o_talks"]]
    mt = sum(per_sku[k]["m_talks"] for k in both); md = sum(per_sku[k]["m_deals"] for k in both)
    ot = sum(per_sku[k]["o_talks"] for k in both); od = sum(per_sku[k]["o_deals"] for k in both)
    print(f"3. mentioned talks: {c[('mentioned', 'talks')]}, the buyer took the lower grade in {c[('mentioned', 'took lower')]}; "
          f"same SKUs ({len(both)}): deal rate mentioned {md}/{mt} ({md / max(mt, 1):.0%}) vs nothing mentioned "
          f"{od}/{ot} ({od / max(ot, 1):.0%})")


if __name__ == "__main__":
    main()
