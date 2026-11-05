"""Which policy numbers the strategist may change, and by how much per run.

The model proposes; this module decides. Only keys in TUNABLE can move, each within a
range and by at most max_step per run, a key changed recently is left alone (cooldown),
a linked pair of keys (LINKED) may not newly break its constraint, and the whole set must
still pass policy.validate. Everything else (cash_floor, end_ts, binding, the llm and forum
on/off switches, warranty grades) is frozen, so no proposal, however it was produced or
whatever text the model read, can touch it.

Each proposal row carries a source. "model" (the default) is bounded as above. "market-rule"
may set only a SYNCED key to the market's published value (strategist/facts.py), clamped to
policy.RANGES. "revert" (an outcome check undoing a change) skips the cooldown, the direction
block and max_changes. No source skips LINKED or validate.
"""
import copy
import math

import policy

# dotted key under policy -> (lo, hi, max_step). Effective bounds also intersect policy.RANGES.
# forum.min_gap_min is not here: it follows the market's post limit (SYNCED).
TUNABLE = {
    "seller.first_ask_list": (0.70, 1.00, 0.05),
    "seller.floor_list": (0.40, 0.80, 0.05),
    "seller.floor_cost_mult": (1.00, 1.30, 0.05),
    "seller.warranty_premium": (0.05, 0.30, 0.05),
    "seller.warranty_cap_6h": (100000, 3000000, 250000),
    "seller.food_clear_uwh_factor": (1.0, 4.0, 0.5),
    "seller.goods_stale_days": (2, 10, 1),
    "stock.inventory_cap_share": (0.15, 0.60, 0.05),
    "stock.per_sku_qty": (1, 5, 1),
    "stock.demand_window_h": (2, 24, 2),
    "stock.regular_min_stock": (0, 3, 1),
    "stock.max_lines": (3, 10, 2),
    "stock.max_qty": (5, 20, 5),
    "stock.goods_max_list": (20000, 100000, 10000),
    "stock.cover_min": (20, 60, 5),
    "stock.cover_safety": (1.0, 3.0, 0.25),
    "stock.focus_cover_mult": (1.0, 3.0, 0.25),
    "stock.focus_cap_share": (0.3, 0.8, 0.1),
    "stock.focus_max": (4, 12, 2),              # lo is at least the current focus list's length
    "buyer.cap_goods_no_warranty": (0.55, 0.85, 0.05),
    "buyer.cap_goods_warranty": (0.80, 1.00, 0.04),
    "buyer.cap_food": (0.85, 1.00, 0.03),
    "buyer.min_surplus_list": (0.0, 0.15, 0.02),
    "buyer.open_list": (0.40, 0.80, 0.05),
    "buyer.max_shops": (1, 5, 1),
    "buyer.target_s": (10, 40, 5),
    "buyer.continue_p": (0.5, 0.95, 0.05),
}

# key -> the strategist/facts.py fact it is set to, only by rows with source "market-rule"
SYNCED = {"forum.min_gap_min": "post_gap_min"}

# name -> (keys, how far the policy is past the constraint; 0 when it holds)
LINKED = {
    "buyer.cap_food + buyer.min_surplus_list <= 1 (above 1 the food cap is 1 - min_surplus_list, "
    "so raising cap_food does nothing)":
        (("buyer.cap_food", "buyer.min_surplus_list"),
         lambda p: max(0.0, p["buyer"]["cap_food"] + p["buyer"]["min_surplus_list"] - 1)),
}
EPS = 1e-9
SOURCES = ("model", "market-rule", "revert")


def bounds(key, pol=None):
    """(lo, hi, max_step) for a tunable key, narrowed by policy.RANGES. With pol, stock.focus_max
    cannot go below the length of the focus list (validate would reject the whole set)."""
    lo, hi, step = TUNABLE[key]
    if key in policy.RANGES:
        rlo, rhi = policy.RANGES[key]
        lo, hi = max(lo, rlo), min(hi, rhi)
    if key == "stock.focus_max" and pol:
        skus = _get(pol, "stock.focus_skus")
        lo = min(max(lo, len(skus) if isinstance(skus, list) else 0), hi)
    return lo, hi, step


def is_int_key(key):
    want = policy.get(policy.DEFAULTS, key)
    return isinstance(want, int) and not isinstance(want, bool)


def _get(pol, key):
    try:
        return policy.get(pol, key)
    except (KeyError, TypeError):
        return None


def tunable_view(pol):
    """The table the prompt shows: current value and limits of every tunable key."""
    rows = []
    for key in TUNABLE:
        lo, hi, step = bounds(key, pol)
        value = _get(pol, key)
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


def _settle(key, old, requested, pol=None):
    """(new value, clamped) after range, step and rounding."""
    lo, hi, step = bounds(key, pol)
    v = min(max(requested, lo), hi)
    v = min(max(v, old - step), old + step)
    return _round(key, v), v != requested


def _sync(key, requested):
    """(new value, clamped) for a market-rule row: policy.RANGES only, no step limit."""
    lo, hi = policy.RANGES.get(key, (-math.inf, math.inf))
    v = min(max(requested, lo), hi)
    return _round(key, v), v != requested


def _round(key, v):
    return int(round(v)) if is_int_key(key) else float(round(v, 4))


def _set(pol, key, value):
    *path, last = key.split(".")
    d = pol
    for k in path:
        d = d[k]
    d[last] = value


def _trial(pol, rows):
    trial = copy.deepcopy(pol)
    for a in rows:
        _set(trial, a["key"], a["new"])
    return trial


def linked_alerts(pol):
    """Names of the LINKED constraints the policy breaks now."""
    out = []
    for name, (_, excess) in LINKED.items():
        try:
            if excess(pol) > EPS:
                out.append(name)
        except (KeyError, TypeError):
            pass
    return out


def _blocked(blocked, key, old, new, now):
    """The why of a direction block on key that this move hits, or None.
    blocked: {key: {"dir": 1 | -1, "until": ts, "why": str}} (an outcome check's revert)."""
    b = (blocked or {}).get(key)
    if not isinstance(b, dict) or not _number(b.get("until")) or now >= b["until"]:
        return None
    if (new > old) - (new < old) != b.get("dir"):
        return None
    return b.get("why") or "reverted"


def guard(proposals, pol, last_change_ts, now, max_changes=3, cooldown_h=3, last=None, blocked=None):
    """(accepted, rejected). accepted rows: key, old, new, requested, clamped, reason, source.
    rejected rows: key, requested, why. last: {key: {"new": value the strategist last wrote, ...}}, so a value
    it wrote that is now outside the range may move back in. A validate failure rejects every accepted row,
    except that market-rule and revert rows that pass on their own are kept."""
    accepted, rejected, seen = [], [], set()
    last_change_ts, last = last_change_ts or {}, last if isinstance(last, dict) else {}
    for p in proposals if isinstance(proposals, list) else []:
        if not isinstance(p, dict):
            rejected.append({"key": None, "requested": None, "why": "malformed"})
            continue
        key, requested, source = p.get("key"), p.get("value"), p.get("source", "model")

        def reject(why):
            rejected.append({"key": key, "requested": requested, "why": why})

        if source not in SOURCES:
            reject("unknown source")
            continue
        if source == "market-rule" and (not isinstance(key, str) or key not in SYNCED):
            reject("not a market-synced key")
            continue
        if source != "market-rule" and (not isinstance(key, str) or key not in TUNABLE):
            reject("frozen")
            continue
        if not _number(requested):
            reject("not a number")
            continue
        if key in seen:
            reject("duplicate")
            continue
        seen.add(key)
        t = last_change_ts.get(key)
        if source == "model" and _number(t) and now - t < cooldown_h * 3600:
            reject("cooldown")
            continue
        old = _get(pol, key)
        if old is None:
            reject("not in policy")
            continue
        if not _number(old):
            reject("current value is not a number")
            continue
        if source == "market-rule":
            new, clamped = _sync(key, requested)
        else:
            lo, hi, _ = bounds(key, pol)
            mine = isinstance(last.get(key), dict) and last[key].get("new") == old
            if not lo <= old <= hi and not mine:    # set by hand outside the tunable range: leave it to the user
                reject(f"current value {old} outside tunable range [{lo}, {hi}]: manual setting")
                continue
            new, clamped = _settle(key, old, requested, pol)   # one of ours outside: clamping moves it back in
        if new == old:
            reject("no change")
            continue
        why = _blocked(blocked, key, old, new, now) if source == "model" else None
        if why:
            reject(f"blocked: {why}")
            continue
        if source == "model" and sum(a["source"] == "model" for a in accepted) >= max_changes:
            reject("over max_changes")
            continue
        accepted.append({"key": key, "old": old, "new": new, "requested": requested, "clamped": clamped,
                         "reason": str(p.get("reason") or "")[:200], "source": source})

    def sink(rows, why):
        rejected.extend({"key": a["key"], "requested": a["requested"], "why": why} for a in rows)
        return [a for a in accepted if a not in rows]

    full = policy.fill_defaults(pol)[0]             # keys missing from the file act at their DEFAULTS
    for name, (keys, excess) in LINKED.items():     # only a new or a worse break is refused
        def worse(rows):
            after = excess(_trial(full, rows))
            return after > EPS and after >= excess(full) - EPS

        touching = [a for a in accepted if a["key"] in keys]
        if touching and worse(accepted):            # the model's rows go first, so a revert is not lost to them
            model = [a for a in touching if a["source"] == "model"]
            rest = [a for a in accepted if a not in model]
            accepted = sink(model if model != touching and not worse(rest) else touching, f"linked: {name}")
    if accepted:
        errors = policy.validate(_trial(full, accepted))
        if errors:
            fixed = [a for a in accepted if a["source"] != "model"]
            keep = fixed if fixed != accepted and not policy.validate(_trial(full, fixed)) else []
            accepted = sink([a for a in accepted if a not in keep], "validate: " + "; ".join(errors))
    return accepted, rejected
