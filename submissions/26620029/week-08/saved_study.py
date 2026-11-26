"""Saved under Patch 3.6 (the user's 11-12 18:50 "지금 나한테 맞는 전략을 짜줘"): what each deal of mine scores and how the top savers differ.

Patch 3.6: a deal scores (list - price) / (list - cost) of the unit sold, 1 at or below cost, 0 when refunded, -1 when it served
nothing; Saved = sum / (deals + 100). The unit's cost comes from the ledger's supply entries (total / qty of the line that
brought that unit in), so deals whose unit came in before the window are left out.
Usage (inside market-kit, from week-08): python -X utf8 saved_study.py HOURS [N]   (N top student savers next to me)
"""
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import monitor as m

PER_HOUR = 17_000


def score(price, lst, cost):
    if cost is None or not lst:
        return None
    if price <= cost:
        return 1.0
    return (lst - price) / (lst - cost) if lst > cost else 0.0


def band(x, edges):
    for e in edges:
        if x < e:
            return f"<{e}"
    return f">={edges[-1]}"


def main():
    hours = float(sys.argv[1])
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cat = json.loads((Path(".market") / "catalog.json").read_text(encoding="utf-8"))
    b = m.get("/api/board")
    st = {(p.get("buyer") or {}).get("name") for p in b["players"] if p.get("student")}
    ys = sorted((y for y in b["buyers"] if y["name"] in st), key=lambda y: -y["saved"])
    pick = [y["name"] for y in ys[:top_n]]
    if m.ME not in pick:
        pick.append(m.ME)
    head = m.get("/api/ledger?after=-1")[-1]["seq"]
    start = head - int(hours * PER_HOUR * 1.1)
    with ThreadPoolExecutor(8) as ex:
        entries = [e for pg in ex.map(m.page, range(start, head + 1, 500)) for e in pg]
    cut = time.time() - hours * 3600
    entries.sort(key=lambda e: e["seq"])
    cost = {}
    for e in entries:
        if e["kind"] == "supply":
            d = e["data"]
            for u in d.get("units") or []:
                cost[u] = (d.get("total") or 0) / max(d.get("qty") or 1, 1)
    refunded = {e["data"].get("unit") for e in entries if e["kind"] == "broken" and (e["data"].get("refunded") or 0) > 0}
    dead = {e["data"].get("unit") for e in entries if e["kind"] == "broken" and not (e["data"].get("refunded") or 0)}
    rows = defaultdict(list)
    mk = defaultdict(list)                   # market-wide (kind, grade) -> cost/list of units settled in the window
    for e in entries:
        d = e["data"]
        if e["kind"] == "settle" and e["ts"] >= cut and d.get("unit") in cost:
            c = cat.get(d.get("sku") or d.get("item")) or {}
            if c.get("list_price"):
                mk[(c.get("kind"), d.get("grade"))].append((cost[d["unit"]] / c["list_price"], d["amount"] / c["list_price"]))
        if e["kind"] != "settle" or e["ts"] < cut or d.get("buyer") not in pick:
            continue
        c = cat.get(d.get("sku") or d.get("item")) or {}
        lst, u = c.get("list_price"), d.get("unit")
        s = score(d["amount"], lst, cost.get(u))
        if u in refunded:
            s = 0.0
        elif u in dead:
            s = -1.0
        rows[d["buyer"]].append(dict(s=s, p=d["amount"] / lst if lst else None, cp=cost[u] / lst if u in cost and lst else None,
                                     kind=c.get("kind"), grade=d.get("grade"), war=bool(d.get("warranty_hours")),
                                     shop=d.get("shop"), lst=lst))
    span = (entries[-1]["ts"] - max(entries[0]["ts"], cut)) / 3600 if entries else hours
    sv = {y["name"]: y for y in ys}
    print(f"== {span:.1f} h of settles, unit cost from supply entries in {len(entries):,} ledger entries")
    print("buyer: board saved, paid | deals/h, known cost | mean score, share at 1 (price <= cost), share <0.5 | paid/list, cost/list")

    def line(xs):
        k = [x for x in xs if x["s"] is not None]
        if not k:
            return "no known cost"
        ms = sum(x["s"] for x in k) / len(k)
        return (f"{len(k)} deals, score {ms:.3f}, at 1 {sum(x['s'] >= 1 for x in k) / len(k):.0%}, under 0.5 {sum(x['s'] < 0.5 for x in k) / len(k):.0%}"
                f" | paid/list {sum(x['p'] for x in k) / len(k):.3f}, cost/list {sum(x['cp'] or 0 for x in k) / len(k):.3f}")

    for n in pick:
        xs = rows.get(n, [])
        y = sv.get(n, {})
        print(f"\n{n}: saved {y.get('saved')}, paid {y.get('paid')} | {len(xs) / span:.0f}/h | {line(xs)}")
        for key, f in (("kind", lambda x: x["kind"]), ("grade", lambda x: x["grade"]),
                       ("list", lambda x: band(x["lst"] or 0, [20_000, 200_000, 1_000_000]))):
            g = defaultdict(list)
            for x in xs:
                g[f(x)].append(x)
            for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
                print(f"   {key} {k}: {line(v)}")
    print("\n== market, settled units with known cost: kind grade: deals, cost/list mean and quartiles, paid/list mean")
    for k in sorted(mk, key=str):
        v = sorted(c for c, _ in mk[k])
        q = lambda f: v[int(f * (len(v) - 1))]
        print(f"   {k[0]} {k[1]}: {len(v)}, cost {sum(v) / len(v):.3f} ({q(0.25):.3f} / {q(0.5):.3f} / {q(0.75):.3f}),"
              f" paid {sum(p for _, p in mk[k]) / len(v):.3f}")
    me = [x for x in rows.get(m.ME, []) if x["s"] is not None]
    print(f"\n== {m.ME}: kind grade, by paid/list band (score mean, deals)")
    g = defaultdict(list)
    for x in me:
        g[(x["kind"], x["grade"], band(x["p"], [0.5, 0.55, 0.6, 0.65, 0.7]))].append(x["s"])
    for k in sorted(g, key=str):
        print(f"   {k[0]} {k[1]} paid {k[2]}: {sum(g[k]) / len(g[k]):.3f}, {len(g[k])}")
    print(f"\n== {m.ME}: by paid/list band (score mean, deals)")
    g = defaultdict(list)
    for x in me:
        g[band(x["p"], [0.4, 0.5, 0.55, 0.6, 0.65, 0.7])].append(x["s"])
    for k in sorted(g):
        print(f"   paid {k}: {sum(g[k]) / len(g[k]):.3f}, {len(g[k])}")
    print(f"== {m.ME}: price / cost of the unit (1.0 = paid exactly cost)")
    g = defaultdict(list)
    for x in me:
        if x["cp"]:
            g[band(x["p"] / x["cp"], [0.9, 1.0, 1.05, 1.1, 1.2, 1.3, 1.5])].append(x["s"])
    for k in sorted(g):
        print(f"   price/cost {k}: {len(g[k])} deals, score {sum(g[k]) / len(g[k]):.3f}")


if __name__ == "__main__":
    main()
