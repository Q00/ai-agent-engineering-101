"""Which policy numbers the strategist may change, and by how much per run.

The model proposes; this module decides. Only keys in TUNABLE can move, each within a
range and by at most max_step per run, a key changed recently is left alone (cooldown),
and the whole set must still pass policy.validate. Everything else (cash_floor, end_ts,
binding, the llm and forum on/off switches, warranty grades) is frozen, so no proposal, however
it was produced or whatever text the model read, can touch it.
"""
import copy
import math

import policy

# dotted key under policy -> (lo, hi, max_step). Effective bounds also intersect policy.RANGES.
TUNABLE = {
    "seller.first_ask_list": (0.70, 1.00, 0.05),
    "seller.floor_list": (0.40, 0.80, 0.05),
    "seller.floor_cost_mult": (1.00, 1.30, 0.05),
    "seller.warranty_premium": (0.05, 0.30, 0.05),
    "seller.warranty_cap_6h": (100000, 1000000, 100000),
    "seller.food_clear_uwh_factor": (1.0, 4.0, 0.5),
    "seller.goods_stale_days": (2, 10, 1),
    "stock.inventory_cap_share": (0.15, 0.45, 0.05),
    "stock.per_sku_qty": (1, 5, 1),
    "stock.demand_window_h": (2, 24, 2),
    "stock.regular_min_stock": (0, 3, 1),
    "stock.max_lines": (3, 10, 2),
    "stock.max_qty": (5, 20, 5),
    "stock.goods_max_list": (20000, 100000, 10000),
    "stock.cover_min": (20, 60, 5),
    "stock.cover_safety": (1.0, 3.0, 0.25),
    "stock.focus_cover_mult": (1.0, 3.0, 0.25),
    "forum.min_gap_min": (5, 60, 5),
    "buyer.cap_goods_no_warranty": (0.55, 0.85, 0.05),
    "buyer.cap_goods_warranty": (0.80, 1.00, 0.04),
    "buyer.cap_food": (0.85, 1.00, 0.03),
    "buyer.min_surplus_list": (0.0, 0.15, 0.02),
    "buyer.open_list": (0.40, 0.80, 0.05),
    "buyer.max_shops": (1, 5, 1),
    "buyer.target_s": (10, 40, 5),
    "buyer.continue_p": (0.5, 0.95, 0.05),
}


def bounds(key):
    """(lo, hi, max_step) for a tunable key, narrowed by policy.RANGES."""
    lo, hi, step = TUNABLE[key]
    if key in policy.RANGES:
        rlo, rhi = policy.RANGES[key]
        lo, hi = max(lo, rlo), min(hi, rhi)
    return lo, hi, step


def is_int_key(key):
    want = policy.get(policy.DEFAULTS, key)
    return isinstance(want, int) and not isinstance(want, bool)


def tunable_view(pol):
    """The table the prompt shows: current value and limits of every tunable key."""
    rows = []
    for key in TUNABLE:
        lo, hi, step = bounds(key)
        try:
            value = policy.get(pol, key)
        except (KeyError, TypeError):
            value = None
        rows.append({"key": key, "value": value, "lo": lo, "hi": hi, "max_step": step})
    return rows


def _number(v):
    """A finite int or float; an int too large for a float (OverflowError in isfinite) is not one."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _settle(key, old, requested):
    """(new value, clamped) after range, step and rounding."""
    lo, hi, step = bounds(key)
    v = min(max(requested, lo), hi)
    v = min(max(v, old - step), old + step)
    clamped = v != requested
    v = int(round(v)) if is_int_key(key) else float(round(v, 4))
    return v, clamped


def _set(pol, key, value):
    *path, last = key.split(".")
    d = pol
    for k in path:
        d = d[k]
    d[last] = value


def guard(proposals, pol, last_change_ts, now, max_changes=3, cooldown_h=3):
    """(accepted, rejected). accepted rows: key, old, new, requested, clamped, reason.
    rejected rows: key, requested, why. A validate failure rejects every accepted row."""
    accepted, rejected, seen = [], [], set()
    last_change_ts = last_change_ts or {}
    for p in proposals if isinstance(proposals, list) else []:
        if not isinstance(p, dict):
            rejected.append({"key": None, "requested": None, "why": "malformed"})
            continue
        key, requested = p.get("key"), p.get("value")

        def reject(why):
            rejected.append({"key": key, "requested": requested, "why": why})

        if not isinstance(key, str) or key not in TUNABLE:
            reject("frozen")
            continue
        if not _number(requested):
            reject("not a number")
            continue
        if key in seen:
            reject("duplicate")
            continue
        seen.add(key)
        last = last_change_ts.get(key)
        if _number(last) and now - last < cooldown_h * 3600:
            reject("cooldown")
            continue
        try:
            old = policy.get(pol, key)
        except (KeyError, TypeError):
            reject("not in policy")
            continue
        if not _number(old):
            reject("current value is not a number")
            continue
        lo, hi, _ = bounds(key)
        if not lo <= old <= hi:                 # set by hand outside the tunable range: leave it to the user
            reject(f"current value {old} outside tunable range [{lo}, {hi}]: manual setting")
            continue
        new, clamped = _settle(key, old, requested)
        if new == old:
            reject("no change")
            continue
        if len(accepted) >= max_changes:
            reject("over max_changes")
            continue
        accepted.append({"key": key, "old": old, "new": new, "requested": requested, "clamped": clamped,
                         "reason": str(p.get("reason") or "")[:200]})
    if accepted:
        trial = copy.deepcopy(pol)
        for a in accepted:
            _set(trial, a["key"], a["new"])
        errors = policy.validate(trial)
        if errors:
            why = "validate: " + "; ".join(errors)
            rejected += [{"key": a["key"], "requested": a["requested"], "why": why} for a in accepted]
            accepted = []
    return accepted, rejected
