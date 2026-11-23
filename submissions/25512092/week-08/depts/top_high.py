"""High-price units over 1.5M cost (person's decision 11/11 ~20:10, with HIGH_MAX 1.5M -> 3.0M). Rules in top_high.json
(read on every call; off without it):
  holding   at most max_units (10) units with cost > min_cost held at once (stock + this plan), at most max_per_sku (2)
            per SKU even for a deep-stock SKU (deep_high's 2-5 is cut to 2 for these)
  warranty  only A grade, and only in a leaf category whose market A grade break rate is at most max_break (6%) with at
            least min_n (30) A grade market sales (11/11 ~20:50 person: the A grade rate, not the whole category's; a
            break counts for the grade of the buyer's last purchase of that SKU before it, as market_db grade_break);
            B and C grade: none. This decides before the sofa and B grade
            rules (depts/sales.warranty_hours). Normal shop case only, like the A grade warranty.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "top_high.json")
MIN_COST = 1_500_000


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_top(cost, r=None):
    r = rules() if r is None else r
    return bool(r.get("enabled")) and (cost or 0) > r.get("min_cost", MIN_COST)


RECENT_H = 48


def last_costs(db_path=None, now=None, hours=RECENT_H):
    """{sku: the highest unit cost of my supplies of it in the last `hours`, else of its most recent supply} (market.db
    my_stock); {} when it cannot be read. One supply mixes grades (09:23 G8ec51935: A 1,537,011 / 1,505,184, A
    1,347,790, later B 1,317,353), so the most recent unit alone can hide the A-grade cost."""
    import sqlite3
    now = now or time.time()
    try:
        d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=30)
        try:
            recent = d.execute("SELECT sku, MAX(cost) FROM my_stock WHERE supplied_at>=? GROUP BY sku",
                               (now - hours * 3600,)).fetchall()
            last = d.execute("SELECT sku, MAX(cost) FROM my_stock m WHERE supplied_at = (SELECT MAX(supplied_at) FROM "
                             "my_stock WHERE sku=m.sku) GROUP BY sku").fetchall()
        finally:
            d.close()
    except sqlite3.Error:
        return {}
    out = {s: c for s, c in last if c}
    out.update({s: c for s, c in recent if c})
    return out


def judge_cost(sku, est, actual):
    """11/12 ~09:50 (person): "over 1.5M" is judged on the larger of the estimate and the SKU's recent actual supply
    cost (09:21: G8ec51935 estimated 1,392,000, supplied at 1,505,184 - 1,665,030)."""
    return max(est or 0, (actual or {}).get(sku) or 0)


def per_sku(r=None, deep=False):
    """Units a SKU over min_cost may hold; deep=True (a deep_high SKU, 11/13 ~16:05 person): deep_per_sku (5) when set."""
    r = rules() if r is None else r
    if deep and r.get("deep_per_sku"):
        return int(r["deep_per_sku"])
    return int(r.get("max_per_sku", 2))


def held(units, r=None):
    """(units with cost > min_cost held, {sku: count})."""
    r = rules() if r is None else r
    per = {}
    for u in units:
        if u.get("grade") != "expired" and (u.get("cost") or 0) > r.get("min_cost", MIN_COST):
            per[u["sku"]] = per.get(u["sku"], 0) + 1
    return sum(per.values()), per


def cap_plan(plan, units, est_cost, log=print, r=None, actual=None, deep=()):
    """The plan with its > min_cost lines cut so that held + planned <= max_units and <= max_per_sku per SKU (in plan
    order: the first lines keep their units). A line's cost: judge_cost (estimate vs last actual supply cost). deep:
    the deep_high SKUs, per SKU deep_per_sku (11/13 ~16:05 person: 5) instead of max_per_sku; max_units unchanged."""
    r = rules() if r is None else r
    if not r.get("enabled"):
        return plan
    actual = last_costs() if actual is None else actual
    n, per = held(units, r)
    room = max(0, int(r.get("max_units", 10)) - n)
    out = []
    for p in plan:
        c = judge_cost(p["sku"], est_cost(p["sku"]), actual)
        if not is_top(c, r):
            out.append(p)
            continue
        cap = per_sku(r, p["sku"] in deep)
        q = min(p["qty"], room, cap - per.get(p["sku"], 0))
        if q <= 0:
            log(f"[top high] {p['sku']} x{p['qty']} (cost {int(c):,}, est. or last supply): over 1.5M held {n}/{r.get('max_units', 10)}, "
                f"this SKU {per.get(p['sku'], 0)}/{cap} -> skip")
            continue
        if q < p["qty"]:
            log(f"[top high] {p['sku']} x{p['qty']} -> x{q} (over 1.5M room {room}, SKU {per.get(p['sku'], 0)}/{cap})")
        room -= q
        per[p["sku"]] = per.get(p["sku"], 0) + q
        out.append(dict(p, qty=q))
    return out


def leaf(u):
    return (u.get("category") or "").split(">")[-1]


def cat_break(db, catalog):
    """({leaf: market break rate}, {leaf: market sales}) over all shops' settles and broken rows, by catalog leaf."""
    lf = {k: (c.get("category") or "").split(">")[-1] for k, c in catalog.items()}
    sold, broke = {}, {}
    for sku, n in db.execute("SELECT sku, COUNT(*) FROM settles GROUP BY sku"):
        if sku in lf:
            sold[lf[sku]] = sold.get(lf[sku], 0) + n
    for sku, n in db.execute("SELECT sku, COUNT(*) FROM broken GROUP BY sku"):
        if sku in lf:
            broke[lf[sku]] = broke.get(lf[sku], 0) + n
    return {k: round(broke.get(k, 0) / n, 4) for k, n in sold.items() if n}, sold


def cat_break_a(db, catalog):
    """({leaf: market A grade break rate}, {leaf: market A grade sales}): A grade sales of all shops by catalog leaf;
    a break is A grade when the buyer's last purchase of that SKU before the break was A grade."""
    lf = {k: (c.get("category") or "").split(">")[-1] for k, c in catalog.items()}
    sold, broke = {}, {}
    for sku, n in db.execute("SELECT sku, COUNT(*) FROM settles WHERE grade='A' GROUP BY sku"):
        if sku in lf:
            sold[lf[sku]] = sold.get(lf[sku], 0) + n
    for sku, n in db.execute("SELECT sku, COUNT(*) FROM (SELECT b.sku sku, (SELECT s.grade FROM settles s WHERE "
                             "s.buyer=b.buyer AND s.sku=b.sku AND s.ts<=b.ts ORDER BY s.ts DESC LIMIT 1) g FROM broken b) "
                             "WHERE g='A' GROUP BY sku"):
        if sku in lf:
            broke[lf[sku]] = broke.get(lf[sku], 0) + n
    return {k: round(broke.get(k, 0) / n, 4) for k, n in sold.items() if n}, sold


CACHE = os.path.join(HERE, "logs", "cat_break_a.json")
CACHE_S = 1800        # the A grade query takes ~2.5 s: at most every 30 min (market_db.refresh runs every restock run)


def cat_break_a_cached(db, catalog, path=None, max_age=CACHE_S, now=None):
    path, now = path or CACHE, now or time.time()
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        if now - c["ts"] < max_age:
            return c["rate"], c["n"]
    except (OSError, ValueError, KeyError):
        pass
    rate, n = cat_break_a(db, catalog)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": now, "rate": rate, "n": n}, f, ensure_ascii=False)
    os.replace(tmp, path)
    return rate, n


def warranty(u, case, base_h, stats, r=None):
    """Warranty hours for a unit over min_cost, or None when this rule does not decide (rule off / not over 1.5M)."""
    r = rules() if r is None else r
    if not is_top(u.get("cost"), r):
        return None
    w = r.get("warranty") or {}
    if case != "normal" or u.get("kind") == "food" or u.get("grade") not in w.get("grades", ["A"]):
        return 0
    lf = leaf(u)
    rate, n = (stats.get("cat_break_a") or {}).get(lf), (stats.get("cat_n_a") or {}).get(lf, 0)
    if rate is None or n < w.get("min_n", 30) or rate > w.get("max_break", 0.06):
        return 0
    from depts import sofa
    if sofa.applies(u):                              # an A grade sofa over 1.5M: the sofa rule's hours (72 h or none)
        return sofa.warranty_for(u)
    return base_h


def log_line(u, h, stats):
    lf = leaf(u)
    return (f"top high {u.get('sku')} grade {u.get('grade')} cost {int(u.get('cost') or 0):,}: {lf} market A break "
            f"{(stats.get('cat_break_a') or {}).get(lf)} (n {(stats.get('cat_n_a') or {}).get(lf, 0)}) -> warranty {h} h "
            f"at {time.strftime('%H:%M')}")
