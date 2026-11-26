"""Hourly market trend (read only; does not move monitor.py's place): what sells now, at what share of list, against my stock.

    python -X utf8 trend.py [hours]     default 3 h, from the public ledger and .market/inventory.json, catalog.json
"""
import collections
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://market.ourolabs.ai"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
ME = "ddolcom"
COST_SHARE = 0.593      # the market's supply cost as a share of list, goods of 1,000,000+, 11-06 (grades drawn 70/20/10)
TOP = 15


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "market-trend"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    now = time.time()
    head = get("/api/ledger?after=-1")[-1]["seq"]
    rows, after = [], head
    while True:                                         # newest first in pages of 500, back to the start of the period
        after = max(after - 500, 0)
        page = get(f"/api/ledger?after={after}")
        rows = [r for r in page if r["seq"] <= (rows[0]["seq"] - 1 if rows else head)] + rows
        if not page or page[0]["ts"] < now - hours * 3600 or after == 0:
            break
    rows = [r for r in rows if r["ts"] >= now - hours * 3600]
    hours = (now - rows[0]["ts"]) / 3600
    catalog = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))
    inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    names = {s["url"]: s.get("name") for s in get("/api/shops")["shops"]}
    # /api/shops knows only each shop's current URL and mine changes at every restart; restock.py keeps all of mine
    mine = set(json.loads(Path("restock_memory.json").read_text(encoding="utf-8"))["customers"]["my_urls"])
    mine |= {url for url, name in names.items() if name == ME}
    surge = {s["sku"]: s["factor"] for s in get("/api/supply-prices")["surge"]}
    stock = collections.Counter(u["sku"] for u in inv["units"] if u.get("grade") != "expired")

    sold = collections.defaultdict(lambda: {"n": 0, "paid": 0, "share": 0.0, "mine": 0, "grade": {}})
    for r in rows:
        d = r["data"]
        if r["kind"] != "settle" or d.get("sku") not in catalog:
            continue
        s = sold[d["sku"]]
        s["n"] += 1
        s["paid"] += d["amount"]
        s["share"] += d["amount"] / catalog[d["sku"]]["list_price"]
        s["mine"] += d["shop"] in mine
        g = s["grade"].setdefault(d.get("grade"), [0, 0])
        g[0] += 1
        g[1] += d["amount"]
    print(f"== {hours:.1f} h, {sum(s['n'] for s in sold.values())} deals; goods by estimated market margin an hour")
    print("deals/h, avg paid/list, list, est margin/h, my deals, my stock, surge, sku name")
    goods = [(k, s) for k, s in sold.items() if catalog[k].get("kind") != "food"]

    def margin(item):
        k, s = item
        return s["n"] / hours * (s["share"] / s["n"] - COST_SHARE * surge.get(k, 1)) * catalog[k]["list_price"]

    for k, s in sorted(goods, key=margin, reverse=True)[:TOP]:
        print(f"{s['n'] / hours:5.1f}, {s['share'] / s['n']:.3f}, {catalog[k]['list_price']:>9,}, {int(margin((k, s))):>10,}, "
              f"{s['mine']}, {stock.get(k, 0)}, {surge.get(k, 1):.3f}, {k} {catalog[k]['name'][:28]}")

    units = [u for u in inv["units"] if u.get("kind") == "goods" and u.get("grade") != "expired"]
    cost = sum(u["cost"] for u in units)
    print(f"\n== burden: goods {len(units)} units, cost {cost:,}, balance {inv['balance']:,}")
    for hrs in (12, 24, 48):
        old = [u for u in units if now - u["supplied_at"] >= hrs * 3600]
        print(f"  unsold {hrs} h+: {len(old)} units, cost {sum(u['cost'] for u in old):,}")
    print("  dearest unsold 12 h+: age h, cost/list, grade, market deals/h, same grade avg paid (COST ABOVE if under my cost), sku name")
    old = [u for u in units if now - u["supplied_at"] >= 12 * 3600]
    for u in sorted(old, key=lambda u: -u["cost"])[:8]:
        n, paid = sold[u["sku"]]["grade"].get(u["grade"], [0, 0])
        avg = paid // n if n else 0
        flag = " COST ABOVE" if n and avg < u["cost"] else ""
        print(f"  {(now - u['supplied_at']) / 3600:.0f}, {u['cost'] / u['list_price']:.2f}, {u['grade']}, "
              f"{sold[u['sku']]['n'] / hours:.1f}, {avg:,}{flag}, {u['sku']} {u['name'][:28]}")


if __name__ == "__main__":
    main()
