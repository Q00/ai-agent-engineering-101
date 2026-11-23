"""Buyer rule for goods that break often (person's decision 11/09 ~11:45, rule 1 only): orders whose SKU is in
CATS (커튼/러그/쿠션/덮개) or SKUS (G81ade83e, 8 of my 37 broken buys).
  warranty first   among the candidate shops, those that sold this category with a warranty in at least
                   WARRANTY_SHARE of their last WINDOW_H market sales (min MIN_SALES) are tried first (score order
                   kept inside each group)
  no warranty      when the shop's first reply shows no warranty, the unit is worth list x (1 - p) with p the
                   market break rate of the SKU (market_stats sku_break, n >= MIN_N; else DEFAULT_P), and the
                   cap becomes min(cap, worth - 1): a deal only while it still saves
Rules in buyer_break.json (read per order; the buyer runs per order).
"""
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "buyer_break.json")
WINDOW_H = 48
MIN_SALES = 5
WARRANTY_SHARE = 0.5


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def leaf(sku, catalog):
    return ((catalog.get(sku) or {}).get("category") or "").split(">")[-1]


def applies(sku, catalog, r=None):
    r = rules() if r is None else r
    return bool(r.get("enabled") and sku and (sku in (r.get("skus") or []) or leaf(sku, catalog) in (r.get("cats") or [])))


def p_break(sku, stats, r=None):
    r = rules() if r is None else r
    if (stats.get("sku_n") or {}).get(sku, 0) >= r.get("min_n", 30) and sku in (stats.get("sku_break") or {}):
        return float(stats["sku_break"][sku])
    return float(r.get("default_p", 0.10))


def value_cap(lp, p):
    """The highest price that still saves: worth = list x (1 - p), minus 1."""
    return max(0, int(lp * (1 - p)) - 1)


def warranty_shares(db, cat_skus, now=None):
    """{shop name: share of its market sales of these SKUs in the last WINDOW_H that carried a warranty} (n >= MIN_SALES)."""
    now = now or time.time()
    if not cat_skus:
        return {}
    q = ",".join("?" * len(cat_skus))
    out = {}
    for shop, n, w in db.execute(f"SELECT shop, COUNT(*), SUM(COALESCE(warranty_hours,0)>0) FROM settles WHERE sku IN ({q}) "
                                 "AND ts>=? GROUP BY shop", (*cat_skus, now - WINDOW_H * 3600)):
        if n >= MIN_SALES:
            out[shop] = w / n
    return out


def warranty_first(cands, shares):
    """Shops with a warranty share >= WARRANTY_SHARE first; the order inside each group is kept."""
    return sorted(cands, key=lambda c: 0 if shares.get(c.get("name"), 0) >= WARRANTY_SHARE else 1)


def order_applies(item, acceptable, r=None):
    """The rule for this order: its category (the order's item) is in CATS or one of its SKUs is in SKUS."""
    r = rules() if r is None else r
    return bool(r.get("enabled") and (item in (r.get("cats") or []) or set(acceptable or []) & set(r.get("skus") or [])))


def order_shops(cands, lf, catalog, log=print, db_path=None):
    skus = [k for k, c in catalog.items() if ((c.get("category") or "").split(">")[-1]) == lf]
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=10)
    try:
        sh = warranty_shares(db, skus)
    finally:
        db.close()
    out = warranty_first(cands, sh)
    log(f"[break rule] warranty first ({lf}): " + ", ".join(f"{c['name']} {sh.get(c['name'], 0):.0%}" for c in out))
    return out
