"""Investigations the strategist may ask for, and the watch list that wakes it early.

The model does not only read a fixed pack: in each answer it may name up to MAX_QUERIES investigations
(whitelisted below, run by code, results attached to the next pack) and up to MAX_WATCH conditions to watch.
Watch conditions are checked every 15 minutes from watch_round.sh (python market_probe.py --check); a condition that
holds wakes the strategist at once instead of at the next hour.

Investigations:
  sku_prices   {"sku"}             settled prices of one SKU in the last ~25 min of the ledger: count, shops,
                                    paid/list quartiles, cheapest shop
  category     {"category"}        market sales per SKU in a category (ledger, decayed), our share of them
  competitor   {"shop"}            a shop's stock (SKUs, units, list value) and its board numbers
  unserved     {}                  orders our buyer saw in 24 h that no shop could serve, by category
  rules        {"term"}            the market's rules (agent.md) around a term
Watch conditions: {"metric": "surge"|"market_units"|"our_stock"|"class_rank", "sku": ..., "op": ">="|"<=",
"value": n, "why": ...}
"""
import argparse
import json
import math
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
MARKET = "https://market.ourolabs.ai"
MAX_QUERIES = 5
MAX_WATCH = 8
WAKE = MD / "strategist_wake"


def get(url, timeout=30):
    with urllib.request.urlopen(f"{MARKET}{url}", timeout=timeout) as r:
        return json.load(r)


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def q_sku_prices(a, cat):
    sku, rows = a.get("sku"), []
    after = -5000
    for _ in range(10):
        page = get(f"/api/ledger?after={after}")
        if not page:
            break
        rows += [e["data"] for e in page if e.get("kind") == "settle" and (e.get("data") or {}).get("sku") == sku]
        after = page[-1]["seq"]
        if len(page) < 500:
            break
    lp = (cat.get(sku) or {}).get("list_price") or 0
    shares = sorted(r["amount"] / lp for r in rows if lp)
    by = defaultdict(list)
    for r in rows:
        by[r.get("shop", "")[-30:]].append(r["amount"])
    q = lambda f: round(shares[int(f * (len(shares) - 1))], 2) if shares else None
    return {"sku": sku, "settled": len(rows), "shops": len(by), "paid_over_list": [q(.25), q(.5), q(.75)],
            "cheapest_shop_median": min((sorted(v)[len(v) // 2] for v in by.values()), default=None)}


def q_category(a, cat):
    st = read(HERE / "restock_state.json", {})
    pm = read(HERE / "product_memory.json", {})
    rows = []
    for sku, n in st.get("sku", {}).items():
        v = cat.get(sku) or {}
        if a.get("category", "") and a["category"] in str(v.get("category", "")):
            ours = sum(h.get("sold", 0) for h in ((pm.get("skus") or {}).get(sku) or {}).get("hours", {}).values())
            rows.append({"sku": sku, "name": v.get("name", "")[:30], "list": v.get("list_price"),
                         "market_per_h": round(n * math.log(2) / 6, 1), "our_sold_48h": ours,
                         "market_units": (st.get("mkt_units") or {}).get(sku, 0),
                         "shops": (st.get("mkt_shops") or {}).get(sku, 0)})
    return sorted(rows, key=lambda r: -r["market_per_h"] * (r["list"] or 0))[:15]


def q_competitor(a, cat):
    shops = get("/api/shops").get("shops", [])
    s = next((x for x in shops if x.get("name") == a.get("shop")), None)
    if not s:
        return {"error": "no such shop"}
    stock = s.get("stock") or {}
    b = next((x for x in get("/api/board?round=3")["shops"] if x["name"] == a.get("shop")), {})
    top = sorted(stock.items(), key=lambda kv: -kv[1] * (cat.get(kv[0]) or {}).get("list_price", 0))[:10]
    return {"shop": a.get("shop"), "skus": len(stock), "units": sum(stock.values()),
            "list_value": sum(q * (cat.get(k) or {}).get("list_price", 0) for k, q in stock.items()),
            "top": [((cat.get(k) or {}).get("name", k)[:26], q) for k, q in top],
            "board": {k: b.get(k) for k in ("all", "book", "revenue", "stars", "deals", "stock_cost")}}


def q_unserved(a, cat):
    c, n = Counter(), Counter()
    for line in open(HERE / "market_intel.jsonl", encoding="utf-8").readlines()[-30000:]:
        if '"type": "order"' in line:
            r = json.loads(line)
            if r["t"] > time.time() - 86400:
                n[r["category"]] += 1
                c[r["category"]] += not r["served"]
    return [{"category": k.split(">")[-1], "orders": n[k], "unserved": v} for k, v in c.most_common(10) if v]


def q_rules(a, cat):
    """Lines of the market's rules (agent.md, re-fetched once a day into .market/agent.md) around a term."""
    path = MD / "agent.md"
    if not path.is_file() or time.time() - path.stat().st_mtime > 86400:
        with urllib.request.urlopen(f"{MARKET}/agent.md", timeout=30) as r:
            path.write_bytes(r.read())
    lines, term = path.read_text(encoding="utf-8").splitlines(), str(a.get("term", "")).lower()
    hits = [i for i, x in enumerate(lines) if term and term in x.lower()][:6]
    return ["\n".join(lines[max(0, i - 1):i + 3]) for i in hits]


QUERIES = {"rules": q_rules, "sku_prices": q_sku_prices, "category": q_category, "competitor": q_competitor,
           "unserved": q_unserved}      # no forum investigation: posts are unverified (user, 11-06)


def run_queries(queries, log):
    cat = read(MD / "catalog.json", {})
    out = []
    for q in (queries or [])[:MAX_QUERIES]:
        kind = q.get("kind")
        if kind not in QUERIES:
            log(f"probe: unknown investigation {kind!r} skipped")
            continue
        try:
            out.append({"query": q, "result": QUERIES[kind](q, cat)})
            log(f"probe: {kind} {json.dumps({k: v for k, v in q.items() if k != 'kind'}, ensure_ascii=False)} done")
        except Exception as e:
            out.append({"query": q, "error": f"{type(e).__name__}: {e}"})
    return out


def watch_value(w):
    inv = read(MD / "inventory.json", {})
    rs = read(HERE / "restock_state.json", {})
    if w["metric"] == "surge":
        return next((x.get("factor") for x in inv.get("surge") or [] if x.get("sku") == w.get("sku")), 1.0)
    if w["metric"] == "market_units":
        return (rs.get("mkt_units") or {}).get(w.get("sku"), 0)
    if w["metric"] == "our_stock":
        return sum(1 for u in inv.get("units", []) if u.get("sku") == w.get("sku"))
    if w["metric"] == "class_rank":
        players = sorted((x for x in get("/api/board?round=3")["players"] if x.get("student")),
                         key=lambda x: -(x.get("student_total") or 0))
        return next((i + 1 for i, x in enumerate(players) if x["player"] == "yms979"), 99)
    return None


def check(watch):
    """Return the first watch condition that holds now, or None."""
    for w in watch[:MAX_WATCH]:
        if w.get("metric") != "class_rank":
            continue                  # 11-11: 21 of 24 early wakes since 11-08 were surge/our_stock flip-flops on two SKUs
        try:
            v = watch_value(w)
        except Exception:
            continue
        if v is not None and (v >= w["value"] if w.get("op") == ">=" else v <= w["value"]):
            return dict(w, now=v)
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    if ap.parse_args().check:
        mem = read(HERE / "strategist_memory.json", {})
        hit = check((mem.get("working") or {}).get("watch") or [])
        if hit:
            WAKE.write_text(json.dumps(hit, ensure_ascii=False))
            print(json.dumps(hit, ensure_ascii=False))
