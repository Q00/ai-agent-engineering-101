"""High-price A-grade warranty on low-break SKUs (person 11/13 ~10:40), added on top of the other warranty rules
(depts/sales.warranty_hours keeps them; this one only turns a 0 into HOURS):
  SKUs: high-price (cost > qc.HIGH_MIN) A-grade units my shop sold in the last window_h (72 h), with >= min_n (20)
        market A-grade sales of the SKU in that window and a market A-grade break rate <= max_break (3%); a break
        counts as A-grade when the buyer's latest earlier purchase of that SKU from that shop was grade A.
        Sofas and food are out. Recomputed every hour by tools/a3_warranty_watch.py into a3_warranty.json.
  hours: 48 h for an A-grade, non-food, non-sofa unit over HIGH_MIN whose SKU is on the list, normal case only.
The seller reads a3_warranty.json on every call (mtime cache), so no restart is needed.
"""
import collections
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "a3_warranty.json")
DEFAULT = {"enabled": False, "hours": 48, "max_break": 0.03, "min_n": 20, "window_h": 72}
_cache = {"path": None, "mtime": None, "r": {}}


def rules(path=None):
    path = path or RULES
    try:
        m = os.path.getmtime(path)
        if _cache["path"] != path or _cache["mtime"] != m:
            with open(path, encoding="utf-8") as f:
                _cache.update(path=path, mtime=m, r=dict(DEFAULT, **json.load(f)))
        return _cache["r"]
    except (OSError, ValueError):
        return dict(DEFAULT)


def hours(u, case, r=None):
    """HOURS when this rule gives the unit a warranty, else 0."""
    from depts import sofa
    from depts.qc import HIGH_MIN
    r = rules() if r is None else r
    if not r.get("enabled") or case != "normal" or not u or u.get("grade") != "A" or u.get("kind") == "food":
        return 0
    if (u.get("cost") or 0) <= HIGH_MIN or u.get("sku") not in (r.get("skus") or {}) or sofa.is_sofa(u["sku"]):
        return 0
    return int(r.get("hours") or 48)


def compute(now=None, db_path=None, r=None, is_sofa=None, kinds=None):
    """{sku: {"n": market A sales, "break": A break rate, "mine": my A sales}} by the rule above."""
    from depts.qc import HIGH_MIN
    if is_sofa is None:
        from depts.sofa import is_sofa
    if kinds is None:
        with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
            kinds = {k: c.get("kind") for k, c in json.load(f).items()}
    r = r or rules()
    now = now or time.time()
    t0 = now - r["window_h"] * 3600
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=30)
    try:
        mine = collections.Counter(k for (k,) in d.execute(
            "SELECT m.sku FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop='choo-chu' AND m.grade='A' "
            "AND m.cost>? AND s.ts>=? AND s.ts<?", (HIGH_MIN, t0, now)))
        skus = [k for k in mine if kinds.get(k) != "food" and not is_sofa(k)]
        if not skus:
            return {}
        q = ",".join("?" * len(skus))
        sales = d.execute(f"SELECT ts, buyer, shop, sku, grade FROM settles WHERE sku IN ({q}) AND ts>=? AND ts<?",
                          (*skus, t0 - 86400, now)).fetchall()
        broken = d.execute(f"SELECT ts, buyer, shop, sku FROM broken WHERE sku IN ({q}) AND ts>=? AND ts<?",
                           (*skus, t0, now)).fetchall()
    finally:
        d.close()
    idx = collections.defaultdict(list)
    n_a = collections.Counter()
    for ts, b, s, k, g in sales:
        idx[(b, s, k)].append((ts, g))
        if ts >= t0 and g == "A":
            n_a[k] += 1
    b_a = collections.Counter()
    for ts, b, s, k in broken:
        c = [x for x in idx[(b, s, k)] if x[0] <= ts]
        if c and max(c)[1] == "A":
            b_a[k] += 1
    return {k: {"n": n_a[k], "break": round(b_a[k] / n_a[k], 4), "mine": mine[k]} for k in skus
            if n_a[k] >= r["min_n"] and b_a[k] / n_a[k] <= r["max_break"]}


def refresh(now=None, path=None, **kw):
    """Recompute the list into the rules file (temp file + replace). Returns the list."""
    path = path or RULES
    r = rules(path)
    skus = compute(now, r=r, **kw)
    r = dict(r, skus=skus, computed_ts=round(now or time.time()))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    os.replace(tmp, path)
    return skus
