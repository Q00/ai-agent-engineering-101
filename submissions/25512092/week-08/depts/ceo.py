"""Which case the shop and the buyer are in, and the rules each case turns on. restock.py calls tick() every
minute; the case is decided at most every CASE_EVERY_S and stored in market.db (table cases).

  shop case (first match wins)
    endgame    from 48 h before the round ends    floor: endgame markdown (pricing.endgame), restock from
                                                   normal rules until 36 h before the end, then none
    cash_low   cash < CASH_RESERVE (1.5M)          floor: cost; no restock (warning in the log under 1M)
    overstock  stock at cost >= STOCK_CAP (4.0M)   floor: cost, or the aging markdown when lower; no restock
               (hysteresis: it ends only when stock falls under STOCK_EXIT, 3.52M)
    normal     otherwise                           floor: max(cost, market average); restock with the
                                                   low/high split; selective 48 h warranty
    always on: food clearance (the lower floor wins) and answers to '구합니다' posts (forum_post.py)

  A case holds at least MIN_HOLD_S (30 min) after it changes, except that entering endgame or cash_low happens
  at once. decide() is the one place that picks a case; everything else reads it through current().

  buyer case
    endgame    from 24 h before the end            cap = budget (the market's mandate still limits it)
    normal     otherwise                           cap = min(budget, market average); SKU by expected
                                                   surplus; C-grade goods only with a warranty; shop score
"""
import time

import pricing
from market_db import ME as ME_NAME, connect, meta  # noqa: F401
from depts.experiments import (ADJUST_EVERY_S, BUYER_COLUMN, MIN_SAMPLES, MIN_WEIGHT, OPEN_TIMEOUT_S,  # noqa: F401
                               VARIANTS, WEIGHT_STEP, adjust, performance, pick, weights)
from depts.sales import WARRANTY_H, WARRANTY_MAX_BREAK, WARRANTY_MIN_N, warranty_hours  # noqa: F401

from limits import CASH_RESERVE, CASH_WARN, STOCK_CAP, STOCK_EXIT  # noqa: E402  hard limits (limits.py)
MIN_HOLD_S = 30 * 60
IMMEDIATE = ("endgame", "cash_low")
CASE_EVERY_S = 300
BUYER_ENDGAME_H = 24


def raw_shop_case(inv, now, prev=None):
    """(case, reason) from the numbers alone, with the overstock hysteresis."""
    cash, stock = inv.get("balance", 0), pricing.held_cost(inv)
    if pricing.endgame_step(now) is not None:
        return "endgame", "48 h before the end"
    import limits
    if limits.auto_cap_on(now=now):                   # 11/09 ~23:40 (person): with the automatic cap, overstock by
        from depts import turnover_case              # turnover only; low cash stops buying (budget), floors stay
        over, why = turnover_case.overstock(now)
        cap = limits.stock_cap_now(cash, stock, now)[0]
        if over:
            return "overstock", f"{why} (auto cap {cap:,})"
        return "normal", f"stock {stock:,}, cash {cash:,}, auto cap {cap:,}; {why}"
    if cash < CASH_RESERVE:
        return "cash_low", f"cash {cash:,} < {CASH_RESERVE:,}"
    import limits                                     # 11/09 ~23:10: the automatic cap (cash + stock - 3M) when on
    cap, exit_, src = limits.stock_cap_now(cash, stock, now)
    tag = " (auto)" if src == "auto" else ""
    if stock >= cap:
        return "overstock", f"stock {stock:,} >= {cap:,}{tag}"
    if prev == "overstock" and stock >= exit_:
        return "overstock", f"stock {stock:,} still >= {exit_:,}{tag} (hysteresis)"
    return "normal", f"stock {stock:,}, cash {cash:,}"


def raw_buyer_case(now):
    if now >= pricing.ROUND_END - BUYER_ENDGAME_H * 3600:
        return "endgame", "24 h before the end"
    return "normal", "before the last 24 h"


def hold(prev, since, new, reason, now):
    """Keep prev for MIN_HOLD_S after it began, unless new is endgame / cash_low."""
    if prev is None or new == prev:
        return new, reason
    if new not in IMMEDIATE and now - since < MIN_HOLD_S:
        return prev, f"{reason}, but {prev} holds until {MIN_HOLD_S // 60} min ({(now - since) / 60:.0f} min so far)"
    return new, reason


def decide(inv, now, state):
    """state: {"shop": case, "shop_since": ts, "buyer": case, "buyer_since": ts} (empty at first).
    Returns ((shop case, reason), (buyer case, reason))."""
    sc, sr = raw_shop_case(inv, now, state.get("shop"))
    bc, br = raw_buyer_case(now)
    return (hold(state.get("shop"), state.get("shop_since", 0), sc, sr, now),
            hold(state.get("buyer"), state.get("buyer_since", 0), bc, br, now))


def load_state(db):
    v = meta(db, "case_state")
    if v:
        return __import__("json").loads(v)
    row = db.execute("SELECT shop_case, buyer_case, ts FROM cases ORDER BY ts DESC LIMIT 1").fetchone()
    return {"shop": row[0], "shop_since": row[2], "buyer": row[1], "buyer_since": row[2]} if row else {}


def current(inv=None):
    """(shop case, buyer case): the last decided one; between ticks nothing changes. With no decision yet
    (or one older than 15 min), decide now from inventory.json without storing it."""
    db = connect()
    try:
        row = db.execute("SELECT shop_case, buyer_case, ts FROM cases ORDER BY ts DESC LIMIT 1").fetchone()
        state = load_state(db)
    finally:
        db.close()
    if row and time.time() - row[2] < 3 * CASE_EVERY_S:
        return row[0], row[1]
    inv = inv if inv is not None else pricing.load_inventory()
    (sc, _), (bc, _) = decide(inv, time.time(), state)
    return sc, bc


def shop_case(inv, now=None):
    return current(inv)[0]


def buyer_case(now=None):
    return current()[1]


def tick(inv, log=print):
    import json
    db = connect()
    try:
        now = time.time()
        if now - float(meta(db, "case_ts", 0)) < CASE_EVERY_S:
            return
        st = load_state(db)
        (sc, sr), (bc, br) = decide(inv, now, st)
        cash, stock = int(inv.get("balance", 0)), pricing.held_cost(inv)
        for role, new, why in (("shop", sc, sr), ("buyer", bc, br)):
            old = st.get(role)
            if old != new:
                held = f", {old} held {(now - st.get(role + '_since', now)) / 60:.0f} min" if old else ""
                log(f"[case] {role} {old or '-'} -> {new}: {why}{held}")
                __import__("decisions").record("ceo", "case", role, new, f"{why}{held}",
                                               limit_hit={"overstock": "STOCK_CAP", "cash_low": "CASH_RESERVE"}.get(new))
                st[role], st[role + "_since"] = new, now
        db.execute("INSERT INTO cases VALUES (?,?,?,?,?,?)", (now, sc, bc, cash, stock, f"{sr} | {br}"))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('case_ts', ?)", (str(now),))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('case_state', ?)", (json.dumps(st),))
        db.commit()
        log(f"[case] shop {sc} ({sr}), buyer {bc}: cash {cash:,}, stock at cost {stock:,}")
        if cash < CASH_WARN:
            log(f"WARNING cash {cash:,} < {CASH_WARN:,}")
    finally:
        db.close()
    adjust(log)


