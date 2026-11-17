"""Who buys from the shop, what it loses to rivals, and what demand it cannot serve. Read by strategist.observe().

Reads only the public ledger (cached incrementally in .market/customer-ledger.json) and the shop's own decision log.
"""
import collections
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MD = ROOT / ".market"
CACHE = MD / "customer-ledger.json"
NAME = "cheapest-in-town"
KEEP_H, MAX_PAGES = 26, 120


def _get(after):
    with urllib.request.urlopen(f"https://market.ourolabs.ai/api/ledger?after={after}", timeout=20) as r:
        return json.load(r)


def _row(e):
    d = e.get("data") or {}
    rc = d.get("receipt") or {}
    if e.get("kind") != "result" or not rc.get("item"):
        return None
    return {"seq": e["seq"], "ts": e["ts"], "buyer": d.get("buyer"), "shop": rc.get("shop"), "sku": rc["item"],
            "price": rc.get("price"), "grade": rc.get("claim")}


def refresh():
    """Bring the cache up to date: forward from the cursor, or a one-time backfill of the last KEEP_H hours."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {"cursor": None, "rows": []}
    rows, cutoff = cache["rows"], time.time() - KEEP_H * 3600
    if cache["cursor"] is None:
        page = _get(-500)
        got, first = list(page), page[0]["seq"]
        for _ in range(MAX_PAGES):
            if not got or got[0]["ts"] < cutoff or first <= 1:
                break
            back = _get(max(0, first - 501))
            back = [e for e in back if e["seq"] < first]
            if not back:
                break
            got, first = back + got, back[0]["seq"]
        cursor = page[-1]["seq"]
    else:
        got, cursor = [], cache["cursor"]
        for _ in range(MAX_PAGES):
            page = _get(cursor)
            if not page:
                break
            got += page
            cursor = page[-1]["seq"]
            if len(page) < 500:
                break
    seen = {r["seq"] for r in rows}
    rows += [r for r in map(_row, got) if r and r["seq"] not in seen]
    rows = [r for r in rows if r["ts"] >= cutoff]
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cursor": cursor, "rows": rows}, ensure_ascii=False))
    tmp.replace(CACHE)
    return rows


def _band(p):
    return "<1만" if p < 1e4 else "1~10만" if p < 1e5 else "10~100만" if p < 1e6 else "100만+"


def observe():
    rows = refresh()
    catalog = json.loads((MD / "catalog.json").read_text())
    lp = lambda sku: (catalog.get(sku) or {}).get("list_price") or 0
    now = time.time()
    out = {"note": "public ledger, our sales vs the whole market; ratio = price / list price",
           "cache_covers_hours": round((now - rows[0]["ts"]) / 3600, 1) if rows else 0}
    for h in (6, 24):
        mine = [r for r in rows if r["shop"] == NAME and r["ts"] > now - h * 3600 and r["price"]]
        market = [r for r in rows if r["ts"] > now - h * 3600]
        rev = collections.Counter()
        deals = collections.Counter()
        ratio = collections.defaultdict(list)
        for r in mine:
            rev[r["buyer"]] += r["price"]; deals[r["buyer"]] += 1
            if lp(r["sku"]):
                ratio[r["buyer"]].append(r["price"] / lp(r["sku"]))
        total = sum(rev.values()) or 1
        bands = collections.defaultdict(lambda: {"deals": 0, "revenue": 0, "ratios": []})
        for r in mine:
            b = bands[_band(lp(r["sku"]) or r["price"])]
            b["deals"] += 1; b["revenue"] += r["price"]
            if lp(r["sku"]):
                b["ratios"].append(r["price"] / lp(r["sku"]))
        out[f"last_{h}h"] = {
            "our_deals": len(mine), "market_deals": len(market), "our_share_of_deals": round(len(mine) / max(1, len(market)), 4),
            "customers": len(rev), "top3_revenue_share": round(sum(v for _, v in rev.most_common(3)) / total, 3),
            "top_customers": [{"buyer": b, "deals": deals[b], "revenue": v, "revenue_share": round(v / total, 3),
                               "mean_ratio": round(sum(ratio[b]) / len(ratio[b]), 3) if ratio[b] else None}
                              for b, v in rev.most_common(6)],
            "by_list_price_band": {k: {"deals": v["deals"], "revenue": v["revenue"],
                                       "mean_ratio": round(sum(v["ratios"]) / len(v["ratios"]), 3) if v["ratios"] else None}
                                   for k, v in sorted(bands.items())},
        }
    # Lost to rivals: we quoted a SKU, no deal, and another shop sold that SKU within 30 minutes.
    since = now - 6 * 3600
    dec = []
    if (ROOT / "logs/shop-decisions.jsonl").exists():
        for line in (ROOT / "logs/shop-decisions.jsonl").read_text(encoding="utf-8").splitlines()[-20000:]:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("ts", 0) > since:
                dec.append(d)
    agreed = {d["task"] for d in dec if d["event"] == "agreed"}
    quotes = {}
    for d in dec:
        if d["event"] == "quote" and d["task"] not in agreed:
            quotes[d["task"]] = d          # last quote of the task
    others = collections.defaultdict(list)
    for r in rows:
        if r["shop"] != NAME and r["ts"] > since:
            others[r["sku"]].append(r)
    lost, gaps = collections.Counter(), collections.defaultdict(list)
    for q in quotes.values():
        hit = next((r for r in others.get(q["sku"], []) if 0 <= r["ts"] - q["ts"] <= 1800), None)
        if hit and q.get("ask"):
            lost[q["sku"]] += 1
            gaps[q["sku"]].append(hit["price"] / q["ask"])
    out["lost_to_rivals_6h"] = {
        "quotes_without_deal": len(quotes), "then_bought_elsewhere": sum(lost.values()),
        "below_floor_exits": sum(d["event"] == "below_floor_exit" for d in dec),
        "top_skus": [{"sku": s, "name": (catalog.get(s) or {}).get("name", "")[:40], "times": n,
                      "rival_price_over_our_ask": round(sorted(gaps[s])[len(gaps[s]) // 2], 3)} for s, n in lost.most_common(6)],
        "note": "rival_price_over_our_ask < 1 means the buyer paid a rival less than our last ask"}
    # Unserved demand: what the market bought in 6 h that we hold no unit of, by revenue.
    held = {u["sku"] for u in json.loads((MD / "inventory.json").read_text()).get("units", [])}
    miss = collections.Counter()
    cnt = collections.Counter()
    for r in rows:
        if r["ts"] > since and r["shop"] != NAME and r["sku"] not in held and r["price"]:
            miss[r["sku"]] += r["price"]; cnt[r["sku"]] += 1
    out["unserved_demand_6h"] = {
        "no_fitting_stock_visits": sum(d["event"] == "no_fitting_stock" for d in dec),
        "top_skus_we_lack": [{"sku": s, "name": (catalog.get(s) or {}).get("name", "")[:40],
                              "category": (catalog.get(s) or {}).get("category", "").split(">")[-1],
                              "list_price": lp(s), "market_deals": cnt[s], "market_revenue": v} for s, v in miss.most_common(8)],
        "note": "no_fitting_stock does not log the SKU asked for; these are SKUs other shops sold that we hold none of"}
    return out


if __name__ == "__main__":
    print(json.dumps(observe(), ensure_ascii=False, indent=1))
