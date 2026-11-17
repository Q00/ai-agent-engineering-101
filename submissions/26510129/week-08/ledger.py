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
CACHE_FILE = "ledger_cache.json"   # the window's settles and the last seq read, so each run fetches only what is new
WINDOW_HOURS = 24
MIN_N = 3            # fewer settles than this and the number is noise; fall back to the category, then to nothing
FIRST_FETCH = 5000   # entries to read on the first run (about 20 minutes of this market); the cache grows from there
MAX_PAGES = 20       # pages of 500 per run: a run never spends more than ~20 calls catching up


def fetch(after=-500, timeout=10):
    r = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else (body.get("entries") or body.get("ledger") or [])


def supplies(entries, now=None, hours=1):
    """Market-wide supply in the last hour per SKU: {sku: units}. Round 3 patch 3.4: once a SKU has been supplied
    more than 50 units market-wide in an hour, its cost rises 0.2% a unit, up to 50%; a crowded SKU is a dear one."""
    now = now or time.time()
    since = now - hours * 3600
    out = {}
    for e in entries or []:
        if e.get("kind") == "supply" and (e.get("ts") or 0) >= since:
            d = e.get("data") or {}
            if d.get("sku"):
                out[d["sku"]] = out.get(d["sku"], 0) + int(d.get("qty") or 1)
    return out


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
        rows.append({"sku": str(sku), "price": float(price), "shop": data.get("shop"), "buyer": data.get("buyer"), "ts": ts,
                     "seq": e.get("seq")})
    return rows


def stats(rows, catalog, now=None, hours=WINDOW_HOURS):
    """Per SKU: settles and median price. Per category: settles and median price / list price."""
    by_sku, by_cat, cat_amounts = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows:
        by_sku[r["sku"]].append(r["price"])
        row = catalog.get(r["sku"]) or {}
        if row.get("category") and isinstance(row.get("list_price"), (int, float)) and row["list_price"] > 0:
            by_cat[row["category"]].append(r["price"] / row["list_price"])
            cat_amounts[row["category"]].append(r["price"])
    # revenue and the typical settled price per category: 11-04 17:10, sofas were 405 of 6347 settles but 591M of
    # 1.1B revenue (median 1.7M each) while kimchi was 751 settles for 17M; a shop that counts settles sells kimchi
    return {
        "at": int(now or time.time()),
        "window_hours": hours,
        "settles": len(rows),
        "skus": {sku: {"n": len(p), "median": round(statistics.median(p))} for sku, p in by_sku.items()},
        "categories": {cat: {"n": len(x), "median_ratio": round(statistics.median(x), 3),
                             "revenue": round(sum(cat_amounts[cat])), "median_amount": round(statistics.median(cat_amounts[cat]))}
                       for cat, x in by_cat.items()},
    }


def refresh(state_dir, catalog, now=None, max_pages=MAX_PAGES, my_units=None):
    """Fetch what the ledger added since the last run, keep a 24 h cache of settles, and rewrite the stats.
    11-04 17:30: one page of 500 entries is under two minutes of this market (40k entries in 2.3 h), so stats from
    a single page saw sofas only now and then; the cache makes the window the full 24 h at one call a minute.
    `my_units` (unit ids of my shop's stock): the settle entries that name one of them are returned under
    "my_sales" - what the market says my shop sold, as opposed to what my seller agreed to (11-17: the seller's own
    log showed 167.9M of revenue on a day the Book rose 1.4M)."""
    now = now or time.time()
    state = Path(state_dir)
    cache = load_json(state / CACHE_FILE) or {"last_seq": None, "rows": []}
    rows, after, pages = list(cache.get("rows") or []), cache.get("last_seq") or -FIRST_FETCH, 0
    supply = list(cache.get("supply") or [])   # [{sku, qty, ts}] of the last hour
    sold = []
    try:
        while pages < max_pages:
            page = fetch(after)
            pages += 1
            if not page:
                break
            rows += settles(page, now)
            if my_units:
                sold += [{"unit": d["unit"], "sku": d.get("sku") or d.get("item"), "price": d.get("amount"),
                          "grade": d.get("grade"), "buyer": d.get("buyer"), "ts": e.get("ts"), "seq": e.get("seq")}
                         for e in page if e.get("kind") == "settle" and (d := e.get("data") or {}).get("unit") in my_units]
            supply += [{"sku": e["data"]["sku"], "qty": int(e["data"].get("qty") or 1), "ts": e.get("ts") or 0}
                       for e in page if e.get("kind") == "supply" and (e.get("data") or {}).get("sku")]
            after = page[-1]["seq"]
            if len(page) < 500:
                break
        rows = [r for r in rows if r["ts"] >= now - WINDOW_HOURS * 3600]
        supply = [s for s in supply if s["ts"] >= now - 3600]
        (state / CACHE_FILE).write_text(json.dumps({"last_seq": after, "rows": rows, "supply": supply}, ensure_ascii=False), encoding="utf-8")
        result = stats(rows, catalog, now)
        result["supplied_1h"] = {}
        for s in supply:
            result["supplied_1h"][s["sku"]] = result["supplied_1h"].get(s["sku"], 0) + s["qty"]
        (state / STATS_FILE).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return {**result, "my_sales": sold} if my_units is not None else result
    except Exception as e:   # market down, no network, odd body: keep the previous stats
        print(f"[ledger] refresh failed: {type(e).__name__}: {str(e)[:100]}; using the previous stats", flush=True)
        return load_stats(state_dir)


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
