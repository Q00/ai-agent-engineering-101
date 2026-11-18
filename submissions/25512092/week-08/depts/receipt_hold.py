"""How long a unit with a receipt stays out of other negotiations (person's decision 11/08 ~13:00).

Before: 15 min for every receipt, paid or not. 26% of receipts are never paid, and while their unit waits, buyers
who name its SKU get "not in stock" (667 such requests in 24 h, 11/08). A payment comes 2.0 s after the receipt at
the median, 4.7 s at p90, 13.8 s at p99 and 32.5 s at most (2,986 paid receipts to 11/08 13:00; 89 settles that
looked hours late were the same unit sold again after the hold).
  hold   the p99 of receipt -> payment, at least MIN_S (3 min), at most MAX_S (15 min); receipt_hold.json, read by
         the seller every negotiation (cached CACHE_S); refresh() recomputes it once a day (analyst process)
  The person also asked for 2 min (p90, at least 2 min) for buyers who leave >= 50% of receipts unpaid; the shop
  never learns who the buyer is during a negotiation (the market passes no buyer name), so that tier is not applied.
  conflicts()  a settle for a receipt that came after the receipt's hold ended while the unit had been sold again
               (another receipt for it, after the hold) -> decision late_pay_conflict, once per settle
"""
import json
import os
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "receipt_hold.json")
MIN_S, MAX_S, DEFAULT_S = 180, 900, 900
CACHE_S = 60
WINDOW_S = 2 * 86400          # the daily recompute looks at the last 48 h
ME = "choo-chu"
_cache = {"t": 0.0, "v": DEFAULT_S}


def hold_s(now=None):
    """Seconds a unit stays held after its receipt (the seller calls this; falls back to 15 min)."""
    now = now or time.time()
    if now - _cache["t"] > CACHE_S:
        try:
            with open(PATH, encoding="utf-8") as f:
                v = float(json.load(f).get("hold_s", DEFAULT_S))
            _cache["v"] = min(MAX_S, max(MIN_S, v))
        except (OSError, ValueError, TypeError):
            _cache["v"] = DEFAULT_S
        _cache["t"] = now
    return _cache["v"]


def _receipts(db, since):
    """{unit: [(receipt ts, task, price), ...] in time order} for my receipts since `since`; the receipt time is the
    shop's 'accept' message (one grouped query: shop_msgs has no index on task)."""
    acc = dict(db.execute("SELECT task, MAX(ts) FROM shop_msgs WHERE who='shop' AND text LIKE 'accept%' AND ts>=? "
                          "GROUP BY task", (since,)).fetchall())
    by = {}
    for task, unit, price in db.execute("SELECT task, unit, price FROM shop_deals WHERE outcome='receipt' AND "
                                        "unit IS NOT NULL AND ts>=?", (since - 900,)):
        if task in acc:
            by.setdefault(unit, []).append((acc[task], task, price))
    for v in by.values():
        v.sort()
    return by


def latencies(db, since=0):
    """Receipt -> payment seconds of my paid receipts since `since` (same unit, same amount, no other receipt of
    the unit in between)."""
    out = []
    for unit, recs in _receipts(db, since).items():
        for i, (rts, task, price) in enumerate(recs):
            s = db.execute("SELECT ts FROM settles WHERE shop=? AND unit=? AND amount=? AND ts>=? ORDER BY ts LIMIT 1",
                           (ME, unit, price, rts - 5)).fetchone()
            if not s or s[0] - rts > 6 * 3600:
                continue
            if any(rts < r[0] < s[0] for r in recs[i + 1:]):
                continue                              # the unit got another receipt first: a resale, not this one
            out.append(s[0] - rts)
    return out


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def compute(db, since=None):
    lat = latencies(db, time.time() - WINDOW_S if since is None else since)
    p = {"n": len(lat), "p50": pct(lat, .5), "p90": pct(lat, .9), "p99": pct(lat, .99), "max": max(lat) if lat else None}
    hold = DEFAULT_S if p["p99"] is None else min(MAX_S, max(MIN_S, p["p99"]))
    return {"hold_s": round(hold), "stats": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in p.items()},
            "rule": "p99 of receipt -> payment, at least 180 s, at most 900 s (person 11/08 ~13:00)", "ts": round(time.time())}


def refresh(log=print, force=False):
    """Once a day (and on force): recompute receipt_hold.json; a change of the hold goes to the decision log."""
    import sqlite3
    import decisions
    try:
        with open(PATH, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    if not force and time.time() - old.get("ts", 0) < 86400:
        return old
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        new = compute(db)
    finally:
        db.close()
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(new, f)
    decisions.record("sales", "receipt_hold", "receipt_hold.hold_s", new["hold_s"],
                     f"daily: {new['stats']} (was {old.get('hold_s')})")
    log(f"receipt hold {old.get('hold_s')} -> {new['hold_s']} s ({new['stats']})")
    return new


def conflicts(db, t0, t1, hold=None):
    """[(settle ts, unit, first task, second task, seconds after the first receipt)] for settles in [t0, t1) that pay
    a receipt after its hold, while another receipt for the same unit went out after that hold."""
    hold = hold or hold_s()
    by = _receipts(db, t0 - 6 * 3600)
    out = []
    for sts, unit, amount, buyer in db.execute("SELECT ts, unit, amount, buyer FROM settles WHERE shop=? AND ts>=? "
                                               "AND ts<? AND unit IS NOT NULL", (ME, t0, t1)).fetchall():
        recs = [r for r in by.get(unit, []) if r[0] <= sts]
        # the payment belongs to the latest receipt with its amount (a resale at the same price is the normal case);
        # a conflict only when that receipt's hold had ended and a later receipt (other price) went out after it
        match = [i for i, r in enumerate(recs) if r[2] == amount]
        if not match:
            continue
        i = match[-1]
        rts, task, price = recs[i]
        later = [r for r in recs[i + 1:] if r[0] > rts + hold]
        if later and sts - rts > hold:
            out.append((sts, unit, task, later[0][1], round(sts - rts), buyer))
    return out


_seen_path = os.path.join(HERE, "logs", "late_pay_seen.json")
TICK_S = 300
_tick = {"t": 0.0}


def conflict_tick(log=print, now=None):
    """Every call (analyst loop): conflicts in the last hour, each settle recorded once as late_pay_conflict."""
    import sqlite3
    import decisions
    now = now or time.time()
    if now - _tick["t"] < TICK_S:
        return 0
    _tick["t"] = now
    try:
        seen = set(json.load(open(_seen_path, encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        found = conflicts(db, now - 3600, now)
    finally:
        db.close()
    new = 0
    for sts, unit, task, task2, secs, buyer in found:
        k = f"{unit}:{int(sts)}"
        if k in seen:
            continue
        seen.add(k)
        new += 1
        decisions.record("sales", "late_pay_conflict", unit, secs,
                         f"payment by {buyer} {secs} s after receipt (task {task}), after the hold; the unit got another "
                         f"receipt after the hold (task {task2}) at {datetime.fromtimestamp(sts):%m-%d %H:%M}")
    if new:
        json.dump(sorted(seen)[-5000:], open(_seen_path, "w", encoding="utf-8"))
        log(f"late_pay_conflict: {new}")
    return new
