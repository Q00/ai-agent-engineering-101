"""Overstock by turnover (person's decision 11/09 ~23:40). While the automatic STOCK_CAP is on (limits.auto_cap_on), the
CEO's shop case (depts/ceo.raw_shop_case) is overstock only when
  1. the median supply -> sale time of my units sold in the last WINDOW_H (6 h) is over MEDIAN_H (3 h), with at least
     MIN_SALES (10) sales, or
  2. of the units supplied in the last SUPPLY_H (24 h, and not before the automatic cap was switched on) that are at
     least OLD_H (6 h) old, the share of their cost still unsold is over OLD_SHARE (25%), judged once their cost is at
     least MIN_COST (5M)
(the same two measures as the cap brake, depts/cap_brake.py). "stock >= cap" and low cash are not overstock then: low
cash only stops buying (the restock budget is cash - 3M), the price floors stay normal.
Thresholds: stock_cap_auto.json "case" ({median_h, window_h, min_sales, old_h, old_share, supply_h, min_cost,
unsold_min, consecutive}).
From 11/09 ~23:50 (person), the same as the cap brake: median 4 h, unsold share 35% AND unsold cost over 5M, and
overstock only on `consecutive` (2) checks in a row at least MIN_GAP_S apart (the CEO case tick is every 5 min); the
first hit sends a Telegram warning (decision overstock_warn) and the case stays normal. State: logs/turnover_case.json.
From 11/10 ~12:10 (person, "shop brakes minimised"): with "warn_only" in the case rules the case is never overstock
(no overstock floors): a hit only sends the Telegram warning, at most once per WARN_EVERY_S while it lasts.
"""
import os
import sqlite3
import statistics
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ME = "choo-chu"
DEFAULT = {"median_h": 3, "window_h": 6, "min_sales": 10, "old_h": 6, "old_share": 0.25, "supply_h": 24,
           "min_cost": 5_000_000, "unsold_min": 0, "consecutive": 1}
STATE = os.path.join(HERE, "logs", "turnover_case.json")
MIN_GAP_S = 240
WARN_EVERY_S = 3600


def _state():
    try:
        import json
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(s):
    import json
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def measure(now=None, since=0, c=None, db_path=None):
    now = now or time.time()
    c = dict(DEFAULT, **(c or {}))
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        ages = [x[0] for x in db.execute(
            "SELECT s.ts - m.supplied_at FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND s.ts>=? "
            "AND m.supplied_at IS NOT NULL", (ME, now - c["window_h"] * 3600))]
        el = db.execute("SELECT m.cost, EXISTS(SELECT 1 FROM settles s WHERE s.unit=m.unit AND s.shop=? AND s.ts<=?) "
                        "FROM my_stock m WHERE m.supplied_at>=? AND m.supplied_at<=?",
                        (ME, now, max(since or 0, now - c["supply_h"] * 3600), now - c["old_h"] * 3600)).fetchall()
    finally:
        db.close()
    el_cost = sum(x or 0 for x, _ in el)
    un_cost = sum(x or 0 for x, sold in el if not sold)
    return {"sales": len(ages), "median_h": round(statistics.median(ages) / 3600, 2) if ages else None,
            "old_cost": el_cost, "old_unsold": un_cost, "old_share": round(un_cost / el_cost, 4) if el_cost else None}


def _warn(why, n, need):
    """need 0: warning only (11/10, person), nothing ever changes."""
    try:
        import alerts
        import decisions
        decisions.record("ceo", "overstock_warn", "shop case", n, (f"check {n}/{need}: " if need else "warning only: ")
                         + "; ".join(why) + " - warning only")
        alerts.send("info", f"overstock_warn:{int(time.time())}", f"과재고(overstock) 판단 경고 {time.strftime('%H:%M')} "
                    + ("(경고만, 자동 조치 없음, normal 유지): " + "; ".join(why) if not need else
                       f"({n}/{need}회, 아직 normal 유지): " + "; ".join(why) + " - 다음 점검(5분 뒤)에도 걸리면 overstock"))
    except Exception:
        pass


def overstock(now=None, r=None, db_path=None):
    """(True / False, reason)."""
    import limits
    r = limits.auto_cap_rules() if r is None else r
    c = dict(DEFAULT, **(r.get("case") or {}))
    m = measure(now, r.get("since") or 0, c, db_path)
    why = []
    if m["sales"] >= c["min_sales"] and m["median_h"] is not None and m["median_h"] > c["median_h"]:
        why.append(f"sale median {m['median_h']} h > {c['median_h']} h ({m['sales']} sales in {c['window_h']} h)")
    if (m["old_cost"] >= c["min_cost"] and m["old_share"] is not None and m["old_share"] > c["old_share"]
            and m["old_unsold"] > c["unsold_min"]):
        why.append(f"{c['old_h']} h+ unsold {m['old_share']:.1%} > {c['old_share']:.0%} and {m['old_unsold']:,} > "
                   f"{c['unsold_min']:,} (of {m['old_cost']:,})")
    now = now or time.time()
    if c.get("warn_only"):                     # 11/10 ~12:10 (person): a warning only, never overstock
        st = _state()
        if why and (not st.get("streak") or now - (st.get("warned") or 0) >= WARN_EVERY_S):
            st = {"ts": round(now), "streak": (st.get("streak") or 0) + 1, "why": why, "warned": round(now)}
            _save(st)
            _warn(why, st["streak"], 0)
        elif not why and st.get("streak"):
            _save({"ts": round(now), "streak": 0, "why": []})
        if why:
            return False, "turnover warning only (no overstock, person 11/10): " + "; ".join(why)
        return False, (f"turnover ok: median {m['median_h']} h ({m['sales']} sales), {c['old_h']} h+ unsold "
                       f"{'-' if m['old_share'] is None else f'{m['old_share']:.1%}'} of {m['old_cost']:,}")
    need = int(c.get("consecutive") or 1)
    if need > 1:
        st = _state()
        if now - (st.get("ts") or 0) >= MIN_GAP_S:     # one count per case check (5 min), not per call
            st = {"ts": round(now), "streak": (st.get("streak", 0) + 1) if why else 0, "why": why}
            _save(st)
            if why and st["streak"] < need:
                _warn(why, st["streak"], need)
        streak = st.get("streak", 0)
        if why and streak < need:
            return False, f"turnover warning {streak}/{need} (not overstock yet): " + "; ".join(why)
    if why:
        return True, "turnover: " + "; ".join(why)
    return False, (f"turnover ok: median {m['median_h']} h ({m['sales']} sales), {c['old_h']} h+ unsold "
                   f"{'-' if m['old_share'] is None else f'{m['old_share']:.1%}'} of {m['old_cost']:,}")
