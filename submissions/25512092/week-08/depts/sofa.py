"""Sofa selling rules (person's decision 11/07 01:30, after the sofa-trial analysis: buyers left after the first ask;
the floor sat at the market average; 60-75% of same-SKU market sales carried a warranty, mostly 72 h).
Sofa = catalog category leaf SOFA_CAT. Switched by sofa_rules.json, read on every call (no restart needed):

  A  first ask   the market median of the SKU (other shops' sales, last 48 h), at most the list price, never under B
  B  floor       max(cost x 1.05, median x 0.92), in every shop case but the endgame
  C  warranty    72 h on A / B grade sofas while "warranty" is true, none on C grade (11/09 ~11:10); the restock
                 check turns it off by itself after 3 sofa warranty refunds or once the refunds exceed the profit
                 on sofa sales (both since "warranty_since", the 11/09 rule change; "since" before it). Until 11/09
                 the check counted no refund (broken.refunded holds the amount, it compared it with 1)
  D  haggling    by code, no harness call: never above the first ask; each counter steps STEP_SHARE of
                 (first ask - floor) down, ending at the floor; accept an offer >= the next counter
  E  buying      (depts/purchasing.py) >= 30 market sales in 48 h, >= 2 sales an hour (last 2 h), cost <= 500,000

Without a median from >= MIN_N market sales the unit falls back to the usual rules (rule != "sofa").
"""
import json
import os
import statistics
import time

SOFA_CAT = "소파/의자/스툴"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "sofa_rules.json")
FLOOR_COST = 1.05
FLOOR_MEDIAN = 0.92
WARRANTY_H = 72
STEP_SHARE = 1 / 3
MIN_N = 5
WINDOW_S = 48 * 3600
BUY_MIN_N = 30
BUY_MIN_PER_H = 2
BUY_MAX_COST = 500_000

_cat = {"mtime": None, "leaf": {}}
_med = {}


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on():
    return bool(rules().get("enabled"))


def warranty_on():
    r = rules()
    return bool(r.get("enabled") and r.get("warranty"))


def warranty_for(u):
    """C (person's decision 11/09 ~11:10): WARRANTY_H for an A / B grade sofa unit while "warranty" is on; a C grade
    unit (refurbished: 4 of the 6 sofa refunds of 11/08) is sold without a warranty ("c_no_warranty", default on)."""
    r = rules()
    if not (r.get("enabled") and r.get("warranty")):
        return 0
    if u.get("grade") == "C" and r.get("c_no_warranty", True):
        return 0
    return WARRANTY_H


def is_sofa(sku):
    path = os.path.join(HERE, ".market", "catalog.json")
    try:
        m = os.path.getmtime(path)
        if m != _cat["mtime"]:
            with open(path, encoding="utf-8") as f:
                cat = json.load(f)
            _cat.update(mtime=m, leaf={k: (c.get("category") or "").split(">")[-1] for k, c in cat.items()})
    except (OSError, ValueError):
        pass
    return _cat["leaf"].get(sku) == SOFA_CAT


MIN_COST = 50_000         # person's decision 11/07 ~02:00: sofa rules only for non-food units costing >= 50,000


def applies(u):
    return bool(u and u.get("sku") and on() and is_sofa(u["sku"]) and u.get("kind") != "food"
                and (u.get("cost") or 0) >= MIN_COST)


def market(sku, now=None):
    """(median, n, warranty share, sales in the last 2 h) of other shops' sales of this SKU, cached 5 min."""
    now = now or time.time()
    hit = _med.get(sku)
    if hit and now - hit[0] < 300:
        return hit[1]
    from market_db import ME, connect
    db = connect()
    try:
        rows = db.execute("SELECT amount, warranty_hours, ts FROM settles WHERE sku=? AND shop!=? AND ts>=? AND ts<=?",
                          (sku, ME, now - WINDOW_S, now)).fetchall()
        last2h = db.execute("SELECT COUNT(*) FROM settles WHERE sku=? AND ts>=? AND ts<=?",
                            (sku, now - 7200, now)).fetchone()[0]
    finally:
        db.close()
    amts = [r[0] for r in rows if r[0]]
    out = (int(statistics.median(amts)) if amts else None, len(amts),
           sum(1 for r in rows if r[1]) / len(rows) if rows else 0.0, last2h)
    _med[sku] = (now, out)
    return out


def floor(u, now=None):
    """(floor, "sofa") or None (no median yet)."""
    med, n, _, _ = market(u["sku"], now)
    if med is None or n < MIN_N:
        return None
    return max(int(u["cost"] * FLOOR_COST), int(med * FLOOR_MEDIAN)), "sofa"


def first_ask(u, reserve, now=None):
    med, _, _, _ = market(u["sku"], now)
    return max(reserve, int(min(u.get("list_price") or med, med)))


def next_counter(deal, reserve):
    """D: the next counter, one step down from the last ask, never above the first ask, never under the floor."""
    first = deal.get("first_ask") or deal["ask"]
    step = max(1, int((first - reserve) * STEP_SHARE))
    return max(reserve, min(first, deal["ask"] - step))


def limits_now(r=None):
    """(units per 20-min window, unsold trial sofas max, lines per run, max cost per unit). 11/09 ~14:35 (person, high
    focus): sofa_rules.json "x2" doubles them all: 1 -> 2, 10 -> 20, 1 -> 2, 500,000 -> 1,000,000 (the high-price
    unit cost cap); the demand rules (>= 30 market sales / 48 h, >= 2 an hour), G5e3784e6 out and C grade without a
    warranty stay."""
    r = r if r is not None else rules()
    if r.get("x3"):                                # 11/09 ~21:20 (person, "more aggressive"): x3 -> 3 / 30 / 3 / 1.5M
        return (3, 30, 3, 3 * BUY_MAX_COST)
    return (2, 20, 2, 2 * BUY_MAX_COST) if r.get("x2") else (1, 10, 1, BUY_MAX_COST)


def buy_ok(sku, cost, now=None):
    """E: (ok, why) for buying one sofa SKU."""
    max_cost = limits_now()[3]
    if cost > max_cost:
        return False, f"cost {int(cost):,} > {max_cost:,}"
    med, n, _, last2h = market(sku, now)
    if n < BUY_MIN_N:
        return False, f"{n} market sales in 48 h < {BUY_MIN_N}"
    if last2h / 2 < BUY_MIN_PER_H:
        return False, f"{last2h / 2:.1f} sales/h < {BUY_MIN_PER_H}"
    return True, f"{n} sales/48 h, {last2h / 2:.1f}/h, median {med}"
