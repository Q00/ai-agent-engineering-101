"""Restock line priority by expected profit (person's decision 11/09 ~14:35, "고가 집중"), switched by line_rank.json
(read every restock run). The request-based lines (depts/request_buy.py, up to 4) keep going first; the other lines
are no longer filled in the scan order (hot, wanted, then by sales in 30 min) but by expected profit per line:
    expected margin x expected units sold x sale price = (market avg - est. cost) x expected units
    expected units = min(planned qty, market sales of the SKU in the last 24 h / 24 x HORIZON_H x my share)
then each line gets what its tier's budget (low / high) and the high-price unit room still allow, up to MAX_LINES.
Every filter that decides whether a SKU may be bought at all (margin, break, high-price guard, first-ask check,
sofa rules, exclusions, perishables) is unchanged: they run before the ranking.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "line_rank.json")
HORIZON_H = 6
_cache = {}


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(r=None):
    return bool((r if r is not None else rules()).get("enabled"))


def market_24h(now=None):
    """{sku: market sales (all shops) in the last 24 h}, cached for 5 min in the process."""
    now = now or time.time()
    if _cache.get("t", 0) > now - 300:
        return _cache["m"]
    from market_db import connect
    db = connect()
    try:
        m = dict(db.execute("SELECT sku, COUNT(*) FROM settles WHERE ts>=? GROUP BY sku", (now - 86400,)).fetchall())
    finally:
        db.close()
    _cache.update(t=now, m=m)
    return m


def expected_units(qty, sales_24h, share, horizon_h=HORIZON_H):
    return min(qty, (sales_24h or 0) / 24 * horizon_h * share)


def score(avg, cost, qty, sales_24h, share, horizon_h=HORIZON_H):
    """Expected profit of the line: margin x expected units x sale price (= (avg - cost) x expected units)."""
    if not avg or avg <= 0:
        return 0.0
    margin = (avg - cost) / avg
    return margin * expected_units(qty, sales_24h, share, horizon_h) * avg


def pick(cands, left, high_room, n_free, log=print, min_low=0, is_low=None):
    """cands: [{sku, qty, tier, cost, score, src}] -> the lines (dicts sku/qty) in score order within the budgets.
    min_low (11/11 ~20:50, depts/normal_min.py): the best-scored normal candidates (is_low(sku)) are taken first, up
    to min_low lines, then the rest by score."""
    out = []
    order = sorted(cands, key=lambda c: -c["score"])
    if min_low and is_low:
        lows = [c for c in order if is_low(c["sku"])]
        first = []
        for c in lows:
            if len(first) >= min_low:
                break
            if int(left[c["tier"]] // c["cost"] if c["cost"] > 0 else c["qty"]) > 0:
                first.append(c)
        if len(first) < min_low:
            log(f"[normal min] only {len(first)} normal candidates for {min_low} lines")
        order = first + [c for c in order if c not in first]
    for c in order:
        if len(out) >= n_free:
            break
        q = min(c["qty"], int(left[c["tier"]] // c["cost"])) if c["cost"] > 0 else c["qty"]
        room = c["tier"] == "high" and not c.get("free")   # 11/13 ~15:40: a deep SKU is out of high_max
        if room:
            q = min(q, high_room)
        if q <= 0:
            continue
        left[c["tier"]] -= q * c["cost"]
        if room:
            high_room -= q
        out.append({"sku": c["sku"], "qty": q})
        log(f"[rank] {c['sku']} ({c['tier']}, {c['src']}): expected profit {int(c['score']):,}, est. cost {int(c['cost']):,} "
            f"-> buy {q}")
    return out
