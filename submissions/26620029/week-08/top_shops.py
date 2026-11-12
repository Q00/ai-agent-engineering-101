"""How the class's top students play (the user's question 11-12 09:50: "1위부터 13위까지 전략을 분석해서 내가 적용 할 수 있는 부분").

Board totals for the top N students and me, then from the ledger's last HOURS: per shop the deals, paid/list, grade mix,
price tiers, top categories, warranty share, stars, breaks, supply spend and expiries; per buyer the deals and paid/list.
Usage (inside market-kit, from week-08): python -X utf8 top_shops.py HOURS [N]
"""
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import monitor as m

PER_HOUR = 17_000


def main():
    hours = float(sys.argv[1])
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 13
    cat = json.loads((Path(".market") / "catalog.json").read_text(encoding="utf-8"))
    b = m.get("/api/board")
    students = sorted((p for p in b["players"] if p.get("student")), key=lambda p: -p["student_total"])
    pick = students[:top_n] + [p for p in students[top_n:] if p["player"] == m.ME]
    shops = {s["name"]: s for s in b["shops"]}
    buyers = {x["name"]: x for x in b["buyers"]}
    print(f"== board, {len(students)} students")
    print("rank player: total (shop, buyer) | shop book, revenue, profit, stock cost, deals, stars_rank, broken/deals, alive"
          " | buyer saved, forum, deals, posts")
    for p in pick:
        s = shops.get((p.get("shop") or {}).get("name")) or {}
        y = buyers.get((p.get("buyer") or {}).get("name")) or {}
        br = (s.get("broken") or 0) / max(s.get("deals") or 1, 1)
        print(f"{students.index(p) + 1:2d} {p['player']}: {p['student_total']:.3f} ({p.get('student_shop')}, {p.get('student_buyer')})"
              f" | {(s.get('book') or 0) / 1e4:,.0f}만, {(s.get('revenue') or 0) / 1e4:,.0f}만, {(s.get('profit') or 0) / 1e4:,.0f}만,"
              f" {(s.get('stock_cost') or 0) / 1e4:,.0f}만, {s.get('deals')}, {s.get('stars_rank')}, {br:.3f}, {s.get('alive')}"
              f" | {y.get('saved')}, {y.get('forum')}, {y.get('deals')}, {y.get('posts')}")
    print("\ncards:")
    for p in pick:
        s = shops.get((p.get("shop") or {}).get("name")) or {}
        print(f"  {p['player']}: {((s.get('card') or {}).get('description') or '')[:230]}")

    url2name = {s["url"]: s["name"] for s in b["shops"] if s.get("url")}
    for s in m.get("/api/shops")["shops"]:
        url2name[s["url"]] = s["name"]
    head = m.get("/api/ledger?after=-1")[-1]["seq"]
    start = head - int(hours * PER_HOUR * 1.1)
    with ThreadPoolExecutor(5) as ex:
        entries = [e for pg in ex.map(m.page, range(start, head + 1, 500)) for e in pg]
    cut = time.time() - hours * 3600
    entries = [e for e in entries if e["ts"] >= cut]
    for e in entries:
        if e["kind"] == "join" and e["data"].get("role") == "shop":
            url2name[e["data"]["url"]] = e["data"]["name"]
    # a settle names the shop by its tunnel URL, which changes on every restart, so old URLs fall back to the order's receipt.
    # The URL comes first: a receipt may carry a display name ("YMS's Market" for yms979), not the board name
    order2shop = {e["data"].get("order"): (e["data"].get("receipt") or {}).get("shop") for e in entries
                  if e["kind"] == "result" and (e["data"].get("receipt") or {}).get("shop")}
    shop_of = lambda d: url2name.get(d.get("shop")) or order2shop.get(d.get("order")) or d.get("shop")
    names = {(p.get("shop") or {}).get("name") for p in pick}
    bnames = {(p.get("buyer") or {}).get("name") for p in pick}
    sh = defaultdict(lambda: defaultdict(list))
    by = defaultdict(lambda: defaultdict(list))
    for e in entries:
        d, k = e["data"], e["kind"]
        if k == "settle":
            n = shop_of(d)
            lp = (cat.get(d.get("sku") or d.get("item")) or {}).get("list_price")
            c = cat.get(d.get("sku") or d.get("item")) or {}
            if n in names:
                sh[n]["settle"].append((d["amount"], lp, d.get("grade"), c.get("kind"), (c.get("category") or "?").split(">")[-1]))
            if d.get("buyer") in bnames and lp:
                by[d["buyer"]]["settle"].append((d["amount"], lp))
        elif k == "result":
            rc = d.get("receipt") or {}
            if rc.get("shop") in names:
                sh[rc["shop"]]["result"].append((d.get("stars"), rc.get("warranty_hours"), rc.get("claim")))
        elif k == "broken":
            n = shop_of(d)
            if n in names:
                sh[n]["broken"].append(d.get("refunded") or 0)
        elif k == "supply":
            if d.get("shop") in names:
                sh[d["shop"]]["supply"].append((d.get("total") or 0, d.get("qty") or 0, d.get("grade")))
        elif k == "expire":
            if d.get("shop") in names:
                sh[d["shop"]]["expire"].append(d.get("cost") or 0)
    span = (entries[-1]["ts"] - entries[0]["ts"]) / 3600 if entries else hours
    print(f"\n== shops in the ledger, last {span:.1f} h (per hour)")
    print("player: deals/h, paid/h, avg paid/list (A, B, C goods), grade mix, food share, tiers low/mid/high, top categories"
          " | warranty share, stars, refunds/h | supply/h, expired/h")
    for p in pick:
        n = (p.get("shop") or {}).get("name")
        r = sh.get(n) or {}
        st = r.get("settle", [])
        if not st:
            print(f"  {p['player']}: no settles")
            continue
        g = Counter(x[2] for x in st)
        pl = lambda gr: [a / lp for a, lp, gg, kk, _ in st if lp and gg == gr and kk != "food"]
        avg = lambda xs: f"{sum(xs) / len(xs):.3f}" if xs else "-"
        tiers = Counter("low" if (lp or 0) <= 200_000 else "mid" if lp <= 1_000_000 else "high" for _, lp, *_ in st)
        val = Counter()
        for a, _, _, _, c in st:
            val[c] += a
        tot = sum(val.values())
        res = r.get("result", [])
        stars = [s for s, _, _ in res if s is not None]
        war = sum(1 for _, w, _ in res if w) / max(len(res), 1)
        food = sum(1 for x in st if x[3] == "food") / len(st)
        sup = r.get("supply", [])
        print(f"  {p['player']}: {len(st) / span:.0f}, {tot / span / 1e4:,.0f}만, ({avg(pl('A'))}, {avg(pl('B'))}, {avg(pl('C'))}),"
              f" A{g['A']}/B{g['B']}/C{g['C']}, food {food:.0%}, {tiers['low']}/{tiers['mid']}/{tiers['high']},"
              f" {', '.join(f'{c} {v / tot:.0%}' for c, v in val.most_common(3))}"
              f" | {war:.0%}, {avg(stars)}, {sum(r.get('broken', [])) / span / 1e4:,.0f}만"
              f" | {sum(t for t, _, _ in sup) / span / 1e4:,.0f}만 ({sum(q for _, q, _ in sup) / span:.0f} units),"
              f" {sum(r.get('expire', [])) / span / 1e4:,.1f}만")
    print(f"\n== buyers in the ledger, last {span:.1f} h: deals/h, avg paid/list")
    for p in pick:
        n = (p.get("buyer") or {}).get("name")
        st = (by.get(n) or {}).get("settle", [])
        print(f"  {p['player']}: {len(st) / span:.0f}, {sum(a / lp for a, lp in st) / max(len(st), 1):.3f}")


if __name__ == "__main__":
    main()
