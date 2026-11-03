"""The market ledger as a demand signal: what sold, where, at what price.

The market publishes neither what sells nor who wants what; settled deals on the public ledger are the one
place it shows (agent.md, Round 3). restock.py refreshes `.market/ledger_stats.json` once per run (one HTTP
call a minute); seller_r3.py only reads that file, so pricing never waits on the network.

    refresh(state_dir, catalog) -> stats     fetch the newest 500 entries, summarize, write the file (old file kept on failure)
    load_stats(state_dir) -> stats           the file, or {} when there is none yet
    market_price(stats, sku, category, list_price) -> (price, n, source) or (None, 0, "none")
"""
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
STATS_FILE = "ledger_stats.json"
WINDOW_HOURS = 24
MIN_N = 3            # fewer settles than this and the number is noise; fall back to the category, then to nothing


def fetch(after=-500, timeout=10):
    r = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else (body.get("entries") or body.get("ledger") or [])


def settles(entries, now=None, hours=WINDOW_HOURS):
    """Normalize settled deals to {sku, price, shop, buyer, ts}.

    Round 3 writes `settle` entries (seen 2026-11-03 20:55, seq 2130137): data = {order, buyer, shop (URL), item (the
    SKU), amount, checkout_hash, unit, sku, name, grade, expires_at, claim, claim_honest, warranty_hours}. The price
    is `amount`. The Round 2 ledger has no settle kind, only `result` with the shop's receipt, read the same way."""
    now = now or time.time()
    since = now - hours * 3600
    rows = []
    for e in entries or []:
        data = e.get("data") or {}
        kind = e.get("kind")
        if kind == "settle":
            sku = data.get("sku") or data.get("item") or (data.get("receipt") or {}).get("item")
            price = next((data.get(k) for k in ("amount", "price") if data.get(k) is not None),
                         (data.get("receipt") or {}).get("price"))
        elif kind == "result":
            receipt = data.get("receipt") or {}
            sku, price = receipt.get("item"), receipt.get("price")
        else:
            continue
        ts = e.get("ts") or 0
        if not sku or not isinstance(price, (int, float)) or price <= 0 or ts < since:
            continue
        rows.append({"sku": str(sku), "price": float(price), "shop": data.get("shop"), "buyer": data.get("buyer"), "ts": ts})
    return rows


def stats(rows, catalog, now=None, hours=WINDOW_HOURS):
    """Per SKU: settles and median price. Per category: settles and median price / list price."""
    by_sku, by_cat = defaultdict(list), defaultdict(list)
    for r in rows:
        by_sku[r["sku"]].append(r["price"])
        row = catalog.get(r["sku"]) or {}
        if row.get("category") and isinstance(row.get("list_price"), (int, float)) and row["list_price"] > 0:
            by_cat[row["category"]].append(r["price"] / row["list_price"])
    return {
        "at": int(now or time.time()),
        "window_hours": hours,
        "settles": len(rows),
        "skus": {sku: {"n": len(p), "median": round(statistics.median(p))} for sku, p in by_sku.items()},
        "categories": {cat: {"n": len(x), "median_ratio": round(statistics.median(x), 3)} for cat, x in by_cat.items()},
    }


def refresh(state_dir, catalog, now=None):
    path = Path(state_dir) / STATS_FILE
    try:
        result = stats(settles(fetch(), now), catalog, now)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result
    except Exception as e:   # market down, no network, odd body: keep the previous stats
        print(f"[ledger] refresh failed: {type(e).__name__}: {str(e)[:100]}; using the previous stats", flush=True)
        return load_stats(state_dir)


def load_stats(state_dir):
    try:
        return json.loads((Path(state_dir) / STATS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def market_price(st, sku, category=None, list_price=None, min_n=MIN_N):
    """What the market paid lately: the SKU's median, else the category's median ratio x list price, else nothing."""
    row = (st.get("skus") or {}).get(sku) or {}
    if row.get("n", 0) >= min_n:
        return float(row["median"]), row["n"], "sku"
    cat = (st.get("categories") or {}).get(category) or {}
    if cat.get("n", 0) >= min_n and isinstance(list_price, (int, float)) and list_price > 0:
        return float(cat["median_ratio"]) * list_price, cat["n"], "category"
    return None, 0, "none"
