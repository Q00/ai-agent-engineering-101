"""my_share for fast hot SKUs (person's decision 11/08 ~20:35). Of the hot list (market_db.HOT_N, top 30), a SKU whose
units of my shop supplied in the last WINDOW_H (24 h) went from supply to sale in FAST_H (1 h) or less at the median
(an unsold unit counts with its age so far; needs MIN_N units) is bought with SHARE (0.8) instead of
purchasing.my_share (0.5). Rules in hot_fast.json (read every run): enabled, share, fast_h, min_n.
"""
import json
import os
import sqlite3
import statistics
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "hot_fast.json")
WINDOW_H = 24
ME = "choo-chu"


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"enabled": False}


def turnover(db, skus, now, min_n=1):
    """{sku: median hours supply -> sale} of my units of these SKUs supplied in the last WINDOW_H."""
    out = {}
    for sku in skus:
        rows = db.execute("SELECT m.supplied_at, (SELECT MIN(x.ts) FROM settles x WHERE x.unit=m.unit AND x.shop=? AND "
                          "x.ts>=m.supplied_at) FROM my_stock m WHERE m.sku=? AND m.supplied_at>=?",
                          (ME, sku, now - WINDOW_H * 3600)).fetchall()
        h = [((sold or now) - sup) / 3600 for sup, sold in rows if sup]
        if len(h) >= min_n:
            out[sku] = statistics.median(h)
    return out


def shares(hot, base, log=print, now=None, db=None):
    """{sku: share} for the hot SKUs that get the higher share (the others keep base)."""
    r = rules()
    if not r.get("enabled") or not hot:
        return {}
    now = now or time.time()
    own = db is None
    db = db or sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        t = turnover(db, hot, now, r.get("min_n", 1))
    finally:
        if own:
            db.close()
    share = max(base, float(r.get("share", 0.8)))
    out = {k: share for k, v in t.items() if v <= r.get("fast_h", 1.0)}
    log(f"[hot fast] {len(out)}/{len(hot)} hot SKUs turn in <= {r.get('fast_h', 1.0)} h -> my_share {share} "
        f"(others {base}): {sorted(out)}")
    return out
