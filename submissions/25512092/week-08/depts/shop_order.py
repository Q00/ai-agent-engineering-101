"""Buyer shop order (person's decision 11/09 ~21:30, to raise the close rate). Two switches in shop_order.json (read
per order; market_buyer.py runs once per order):
  stock_first  the market's shop list already keeps only shops whose offers show an acceptable SKU with qty > 0, so
               stock cannot be checked further before negotiating; instead a shop that told us "no stock" for the
               SKU we would buy there within the last NO_STOCK_H (1 h) goes after the others (logs/no_stock.jsonl,
               written by the buyer on every no-stock reply)
  rate_order   inside each group, shops by our close rate with them over the last WINDOW_H (24 h, buyer_deals
               deal / (deal + fail)); shops with fewer than MIN_N (10) tries get the median of the others
Both on: the whole allowed list is reordered before the top 3 candidates are cut (was: shop score order). The
blocked shops (cheated me, dishonest claims), the break-rule warranty order and the saved guards are unchanged.
tools/saved_guard_watch.py switches both off (only these two) when the close rate or deals per hour drop.
"""
import json
import os
import statistics
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "shop_order.json")
LOG = os.path.join(HERE, "logs", "no_stock.jsonl")
TAG = "shop_order"
NO_STOCK_H, WINDOW_H, MIN_N = 1.0, 24.0, 10


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(r=None, now=None):
    r = rules() if r is None else r
    return bool(r.get("enabled")) and (r.get("since") or 0) <= (now or time.time()) \
        and bool(r.get("stock_first") or r.get("rate_order"))


def note_no_stock(shop, sku, now=None, path=None):
    try:
        with open(path or LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(now or time.time(), 1), "shop": shop, "sku": sku}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_no_stock(now=None, h=NO_STOCK_H, path=None):
    """{(shop, sku)} with a no-stock reply in the last h hours."""
    now = now or time.time()
    out = set()
    try:
        with open(path or LOG, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if now - e.get("ts", 0) <= h * 3600:
                    out.add((e.get("shop"), e.get("sku")))
    except OSError:
        pass
    return out


def rates(now=None, h=WINDOW_H, db_path=None):
    """{shop: (close rate, tries)} from buyer_deals over the last h hours (deal / (deal + fail))."""
    import sqlite3
    now = now or time.time()
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        rows = db.execute("SELECT shop, SUM(outcome='deal'), COUNT(*) FROM buyer_deals WHERE ts>=? AND outcome IN "
                          "('deal','fail') GROUP BY shop", (now - h * 3600,)).fetchall()
    finally:
        db.close()
    return {s: (d / n, n) for s, d, n in rows if n}


def reorder(cands, sku_of, now=None, r=None, no_stock=None, rate=None):
    """cands in their shop-score order -> (new order, {name: (pushed back, rate used, tries)}). sku_of(c): the SKU the
    buyer would ask for at c (or None)."""
    r = rules() if r is None else r
    now = now or time.time()
    stock_first, rate_order = bool(r.get("stock_first")), bool(r.get("rate_order"))
    ns = (recent_no_stock(now, r.get("no_stock_h", NO_STOCK_H)) if no_stock is None else no_stock) if stock_first else set()
    rt = (rates(now, r.get("window_h", WINDOW_H)) if rate is None else rate) if rate_order else {}
    min_n = r.get("min_n", MIN_N)
    known = [x for x, n in rt.values() if n >= min_n]
    mid = statistics.median(known) if known else 0.5
    info = {}
    for i, c in enumerate(cands):
        x, n = rt.get(c["name"], (None, 0))
        used = x if n >= min_n else mid
        back = False
        if stock_first:
            try:
                back = (c["name"], sku_of(c)) in ns
            except Exception:
                back = False
        info[c["name"]] = (back, round(used, 3), n)
        c["_so"] = (back, -used if rate_order else 0, i)
    out = sorted(cands, key=lambda c: c["_so"])
    for c in out:
        c.pop("_so", None)
    return out, info
