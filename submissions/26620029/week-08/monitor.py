"""monitor.py - the shop's three-hourly check for Round 3. Reads the market and my files, changes no policy.

Run from this folder:  python -X utf8 monitor.py   (prints the report; keeps its place in .market/monitor_state.json)
It shows what a policy decision needs: is the kit alive, what sold since the last check at what margin, what the
market bought that I do not stock, breaks by grade, ranks, and whether the stock can sell before the deadline.
"""
import collections as C
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
MARKET_DIR = HERE / ".market"
STATE = MARKET_DIR / "monitor_state.json"
LOG = HERE / "logs" / "market-kit.log"
MARKET = "https://market.ourolabs.ai"
ME = "ddolcom"
KST = timezone(timedelta(hours=9))
DEADLINE = datetime(2026, 11, 17, 23, 59, tzinfo=KST).timestamp()
FIRST_LOOKBACK = 45_000      # ledger entries read on the very first run, about three hours


def get(path):
    req = urllib.request.Request(MARKET + path, headers={"User-Agent": "shop-monitor"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def page(after):
    for _ in range(3):
        try:
            return get(f"/api/ledger?after={after}")
        except Exception:
            time.sleep(1)
    print(f"WARNING: ledger page after {after} could not be read; numbers below miss up to 500 entries")
    return []


def rank(rows, key, name=ME):
    live = sorted((r for r in rows if r.get(key) is not None), key=lambda r: -r[key])
    return next((i + 1 for i, r in enumerate(live) if r["name"] == name), None), len(live)


def main():
    now = time.time()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    catalog = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))
    units = inv["units"]
    ucost = state.get("ucost", {})
    url2name = state.get("url2name", {})
    for u in units:
        ucost[u["unit"]] = u["cost"]

    head = get("/api/ledger?after=-1")[-1]["seq"]
    start = state.get("seq", head - FIRST_LOOKBACK)
    with ThreadPoolExecutor(5) as ex:
        entries = [e for p in ex.map(page, range(start, head + 1, 500)) for e in p]
    for s in get("/api/shops")["shops"]:
        url2name[s["url"]] = s["name"]
    for e in entries:
        d = e["data"]
        if e["kind"] == "join" and d.get("role") == "shop":
            url2name[d["url"]] = d["name"]
        elif e["kind"] == "supply" and d.get("shop") == ME:
            for u in d.get("units") or []:
                ucost[u] = d["total"] / (d.get("qty") or 1)

    last_write = LOG.stat().st_mtime
    hours_left = (DEADLINE - now) / 3600
    print(f"== {datetime.now(KST):%m-%d %H:%M} KST, {hours_left:.1f} h to the deadline, ledger #{start} to #{head}")
    print(f"kit log last written {now - last_write:.0f} s ago" + ("  <-- KIT MAY BE DOWN" if now - last_write > 600 else ""))

    board = get("/api/board")
    shop = next(s for s in board["shops"] if s["name"] == ME)
    buyer = next(b for b in board["buyers"] if b["name"] == ME)
    player = next((i + 1, p) for i, p in enumerate(board["players"]) if p["player"] == ME)
    print(f"balance {inv['balance']:,}  profit {shop['profit']:,}  revenue {shop['revenue']:,}  deals {shop['deals']}  stars {shop['stars']}  "
          f"broken {shop['broken']}  refunds {shop['refunds']}  bankruptcies {shop['bankruptcies']}")
    for key in ("all", "book", "profit", "revenue", "stars"):   # the market's rules since 2026-11-06: ALL uses book, not cash
        r, n = rank(board["shops"], key)
        print(f"  shop {key} {shop.get(key)}: rank {r} of {n}")
    r, n = rank(board["buyers"], "all")
    print(f"  buyer all {buyer['all']}: rank {r} of {n}; surplus {buyer['surplus']:,}, deals {buyer['deals']}")
    for key in ("saved", "forum", "deals", "posts"):            # buyer ALL weighs them 0.5, 0.3, 0.1, 0.1
        r, n = rank(board["buyers"], key)
        print(f"  buyer {key} {buyer.get(key)}: rank {r} of {n}")
    print(f"  players rank {player[0]} of {len(board['players'])}, total {player[1]['total']}, counted shop {player[1]['shop']['name']!r} all {player[1]['shop']['all']}")
    # the midterm ranks students among students (rules, Patch 3.3); stars rank by one mean per reviewing account (Patch 3.4)
    students = sorted((p for p in board["players"] if p.get("student")), key=lambda p: -p["student_total"])
    me = next(p for p in students if p["player"] == ME)
    print(f"  CLASS rank {students.index(me) + 1} of {len(students)}, pct {me['student_pct']}, total {me['student_total']}, shop {me['student_shop']}, buyer {me['student_buyer']}")
    shops = [s for s in board["shops"] if s["name"] in {p["shop"]["name"] for p in students if p.get("shop")} and s.get("all") is not None]
    buyers = [b for b in board["buyers"] if b["name"] in {p["buyer"]["name"] for p in students if p.get("buyer")} and b.get("all") is not None]

    def class_pct(rows, row, key):
        return sum((r.get(key) if r.get(key) is not None else float("-inf")) < row[key] for r in rows) / max(len(rows) - 1, 1)

    print("  class pct " + ", ".join(f"{k} {shop.get(k)} {class_pct(shops, shop, k):.3f}" for k in ("book", "revenue", "stars_rank")))
    print("  class pct " + ", ".join(f"{k} {buyer.get(k)} {class_pct(buyers, buyer, k):.3f}" for k in ("saved", "forum", "deals", "posts")))
    if state.get("all") is not None:
        print(f"  since last check: shop all {state['all']} -> {shop['all']}, profit {state['profit']:,} -> {shop['profit']:,}, buyer all {state['buyer_all']} -> {buyer['all']}")

    settles = [e for e in entries if e["kind"] == "settle"]
    mine = [e["data"] for e in settles if url2name.get(e["data"]["shop"]) == ME]
    orders = {d["order"]: d for d in mine}
    breaks = [e["data"] for e in entries if e["kind"] == "broken" and url2name.get(e["data"]["shop"]) == ME]
    span = (entries[-1]["ts"] - entries[0]["ts"]) / 3600 if entries else 0
    paid = sum(d["amount"] for d in mine)
    known = [d for d in mine if d.get("unit") in ucost]
    margin = sum(d["amount"] - ucost[d["unit"]] for d in known)
    refunded = sum(d.get("refunded") or 0 for d in breaks)
    spent = sum(e["data"].get("total") or 0 for e in entries if e["kind"] == "supply" and e["data"].get("shop") == ME)
    print(f"\n== my sales in this window ({span:.1f} h)")
    print(f"deals {len(mine)}, paid {paid:,}, margin {margin:,.0f} on {len(known)} with known cost, refunds {refunded:,} on {len(breaks)} breaks, restock spend {spent:,}")

    def cat_of(d):
        return (catalog.get(d["sku"], {}).get("category") or "?").split(">")[-1]

    market_paid = C.Counter()
    for e in settles:
        market_paid[cat_of(e["data"])] += e["data"]["amount"]
    total_market = sum(market_paid.values()) or 1
    my_cat, my_margin, my_n = C.Counter(), C.Counter(), C.Counter()
    for d in mine:
        my_cat[cat_of(d)] += d["amount"]
        my_n[cat_of(d)] += 1
        if d.get("unit") in ucost:
            my_margin[cat_of(d)] += d["amount"] - ucost[d["unit"]]
    print("category: market share of value | my deals, paid, margin")
    for c, v in market_paid.most_common(10):
        print(f"  {v / total_market:5.1%} {c}: {my_n[c]}, {my_cat[c]:,}, {my_margin[c]:,.0f}")

    print("\n== breaks in this window")
    for d in breaks:
        o = orders.get(d.get("order"), {})
        print(f"  refunded {d.get('refunded')}, {d.get('hours_after_sale')} h after sale, warranty {d.get('warranty_hours')}, grade {o.get('grade', 'sold before this window')}, "
              f"{catalog.get(o.get('sku'), {}).get('name', '')[:30]}")
    goods = [d for d in mine if catalog.get(d["sku"], {}).get("kind") == "goods"]
    for g in "ABC":
        x = [d for d in goods if d.get("grade") == g]
        print(f"  grade {g} goods sold {len(x)}, paid {sum(d['amount'] for d in x):,}, margin {sum(d['amount'] - ucost[d['unit']] for d in x if d.get('unit') in ucost):,.0f}, "
              f"with warranty {sum(1 for d in x if d.get('warranty_hours'))}, broke {sum(1 for b in breaks if b.get('order') in {d['order'] for d in x})}")

    stock_cost = sum(u["cost"] for u in units)
    print(f"\n== stock: {len(units)} units, cost {stock_cost:,.0f}, storage about {stock_cost * inv['storage_per_day']:,.0f} a day")
    print(f"grades {dict(C.Counter(u['grade'] for u in units))}, goods {sum(1 for u in units if u['kind'] == 'goods')}, food {sum(1 for u in units if u['kind'] == 'food')}")
    by_cat = C.defaultdict(list)
    for u in units:
        by_cat[u["category"].split(">")[-1]].append(u)
    for c, x in sorted(by_cat.items(), key=lambda kv: -sum(u["cost"] for u in kv[1]))[:6]:
        print(f"  {len(x)} units, cost {sum(u['cost'] for u in x):,.0f}, {c}")
    food = [u for u in units if u["kind"] == "food" and u.get("expires_at")]
    soon = [u for u in food if u["expires_at"] - now < 72 * 3600]
    old = [u for u in units if u["kind"] == "goods" and now - u["supplied_at"] > 48 * 3600]
    print(f"food under 72 h of shelf life: {len(soon)}, cost {sum(u['cost'] for u in soon):,.0f}; goods unsold over 48 h: {len(old)}, cost {sum(u['cost'] for u in old):,.0f}")
    print(f"next restock window: {inv['restock']}, in {(inv['restock'].get('opens_at', now) - now) / 60:.1f} min")
    cost_sold = sum(ucost[d["unit"]] for d in known)
    per_hour = cost_sold / span if span else 0
    cover = stock_cost / per_hour if per_hour else None
    print(f"cost of goods sold {per_hour:,.0f} an hour: the stock lasts {cover:.1f} h at this pace, {hours_left:.1f} h left" if cover else "no sales to pace the stock against")
    if cover and hours_left < cover * 2:
        print("  ENDGAME: book counts unsold goods at cost, so do not sell below cost; food past its date leaves the book. Stop short-lived food.")

    print("\n== first-ask trial (log since the last rotation): arm, opens, deals, conversion, avg paid/list")
    arms = {}
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if "arm low)" not in line and "arm list)" not in line:
            continue
        parts = line.split()
        lst = int(parts[parts.index("(list") + 1].rstrip(",)"))
        a = arms.setdefault(("low" if "arm low)" in line else "list", "1M+" if lst >= 1_000_000 else "under 1M"), [0, 0, 0.0])
        if "[price] open" in line:
            a[0] += 1
        elif "[price] deal" in line:
            a[1] += 1
            a[2] += int(parts[parts.index("at") + 1]) / lst
    for k, a in sorted(arms.items()):
        print(f"  {k[0]} {k[1]}: {a[0]}, {a[1]}, {a[1] / a[0] if a[0] else 0:.1%}, {a[2] / a[1] if a[1] else 0:.3f}")

    bought = [e["data"] for e in settles if e["data"].get("buyer") == ME and catalog.get(e["data"]["sku"], {}).get("list_price")]
    broke = {e["data"]["order"] for e in entries if e["kind"] == "broken" and e["data"].get("buyer") == ME}
    disc = [1 - d["amount"] / catalog[d["sku"]]["list_price"] for d in bought]
    print(f"\n== my buying in this window: deals {len(bought)}, {len(bought) / span if span else 0:.1f} an hour, "
          f"avg discount {sum(disc) / len(disc) if disc else 0:.3f}, breaks reported {len(broke)}")

    in_stock = {u["sku"] for u in units}
    sku_n, sku_paid = C.Counter(), C.Counter()
    for e in settles:
        sku_n[e["data"]["sku"]] += 1
        sku_paid[e["data"]["sku"]] += e["data"]["amount"]
    print("\n== market's top SKUs by value in this window (deals, paid, avg, in my stock)")
    for s, v in sku_paid.most_common(12):
        c = catalog.get(s, {})
        print(f"  {sku_n[s]}, {v:,}, {v // sku_n[s]:,}, {'yes' if s in in_stock else 'NO'}, list {c.get('list_price')}, {s} {(c.get('name') or '')[:34]}")

    MARKET_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"seq": head, "ucost": ucost, "url2name": url2name, "all": shop["all"], "profit": shop["profit"], "buyer_all": buyer["all"]}), encoding="utf-8")


if __name__ == "__main__":
    main()
