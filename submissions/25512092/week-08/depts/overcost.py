"""Over-cost high-price units (person's decision 11/09 ~14:50): a high-price unit (cost > qc.HIGH_MIN, not a sofa)
whose cost is above the market average of its SKU, or one of the units listed at the 15:05 high-focus switch whose
floor was above the market average (overcost_high.json "units"):
  - floor max(cost x 1.00, 48 h median x 0.80) instead of cost x 1.05 (depts/high_ask.floor), never under cost,
    except the old stoploss (11/09 ~15:15): cost at most 2% above the average -> a lower floor down to the average
  - not counted in the high-price unit limit (high_max, depts/purchasing.make_plan)
  - not counted in the high-price brake (tools/high_focus_rollout.py)
The first time a unit qualifies: decision overcost_high (purchasing runs every minute and calls mark()).
overcost_high.json is read on every call; logs/overcost_units.json keeps the units seen {unit: ts}.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "overcost_high.json")
SEEN = os.path.join(HERE, "logs", "overcost_units.json")
COST_FACTOR = 1.00
STOPLOSS_SHARE = 0.02      # 11/09 ~15:15 (person): cost at most 2% above the average -> the floor may be the average
#                            (the old stoploss, depts/sales.stoploss: 2 days of 1% storage); more than 2%: never under cost


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _high(u):
    from depts.qc import HIGH_MIN
    return (u.get("cost") or 0) > HIGH_MIN


def _sofa(u):
    from depts import sofa
    try:
        return sofa.applies(u)
    except Exception:
        return False


def is_overcost(u, avg, r=None):
    r = r if r is not None else rules()
    if not r.get("enabled") or not _high(u) or _sofa(u):
        return False
    if u.get("unit") in set(r.get("units") or []):
        return True
    return bool(avg) and (u.get("cost") or 0) > avg


def seen():
    try:
        with open(SEEN, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def mark(units, avg_of, log=print, now=None):
    """The over-cost units among the held ones (set of unit ids); a decision for each one seen the first time."""
    r = rules()
    if not r.get("enabled"):
        return set()
    now = now or time.time()
    s, out, new = seen(), set(), False
    for u in units:
        if u.get("grade") == "expired":
            continue
        avg = avg_of(u["sku"])
        if not is_overcost(u, avg, r):
            continue
        out.add(u["unit"])
        if u["unit"] not in s:
            s[u["unit"]] = round(now)
            new = True
            why = "listed at the 15:05 switch (floor above avg)" if u["unit"] in set(r.get("units") or []) else "cost > avg"
            try:
                import decisions
                decisions.record("purchasing", "overcost_high", u["sku"], [u["unit"], int(u.get("cost") or 0),
                                                                          int(avg) if avg else None],
                                 f"{why}: floor cost x {COST_FACTOR:.2f}, out of high_max and the high-price brake")
            except Exception:
                pass
            log(f"[overcost] {u['sku']} {u['unit']}: cost {int(u.get('cost') or 0):,} vs avg {int(avg) if avg else '-'} ({why})")
    if new:
        with open(SEEN, "w", encoding="utf-8") as f:
            json.dump(s, f)
    return out
