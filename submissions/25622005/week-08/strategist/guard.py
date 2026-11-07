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
block and max_changes. "bounds" (bounds_rows) moves a value outside its TUNABLE range to the nearest
bound, with no step, cooldown, block or max_changes. "migration" (strategist/migrate.py: a default the code
release changed) and "linked" (linked_rows: buyer.cap_food down to 1 - buyer.min_surplus_list) set a tunable
key to their value within its bounds, also with no step, cooldown, block or max_changes. "objective"
(strategist/objective.py, score_twin) sets buyer.min_kept the same way, stepped by that module. No source skips
LINKED or validate.
A model raise of seller.floor_list above the market's 24 h clearing price for goods (p_clear, median
paid/list) is refused. A key in policy.DEFAULTS but missing from the file acts at its DEFAULT, so its
old value is the DEFAULT (strategist/skillfile.py inserts the line when it is written).
"""
import copy
import math

import policy

# dotted key under policy -> (lo, hi, max_step). Effective bounds also intersect policy.RANGES.
# forum.min_gap_min is not here: it follows the market's post limit (SYNCED).
TUNABLE = {
    "seller.first_ask_list": (0.70, 1.00, 0.05),
    "seller.first_ask_big_list": (0.60, 0.95, 0.03),   # big-ticket goods clear at 0.67-0.72 of list
    "seller.floor_list": (0.40, 0.70, 0.05),       # the market clears goods at 0.71-0.78 of list
    "seller.floor_cost_mult": (1.00, 1.15, 0.05),
    "seller.warranty_premium": (0.05, 0.30, 0.05),
    "seller.warranty_premium_floor": (0.0, 0.10, 0.01),   # breakage-priced warranty: the floor of the premium
    "seller.warranty_q_mult": (1.0, 3.0, 0.25),           # safety on the measured breakage upper bound
    "seller.warranty_cap_6h": (100000, 20000000, 1000000),
    "seller.food_clear_uwh_factor": (1.0, 4.0, 0.5),
    "seller.goods_stale_days": (2, 10, 1),
    "seller.stale_floor_cost_mult": (0.60, 1.00, 0.10),   # clearance: stale goods floor as a share of cost
    "stock.inventory_cap_share": (0.15, 0.90, 0.05),   # Patch 3.3: stock counts at cost in Book, so this is a liquidity limit
    "stock.per_sku_qty": (1, 10, 1),
    "stock.demand_window_h": (2, 24, 2),
    "stock.regular_min_stock": (0, 3, 1),
    "stock.max_lines": (3, 10, 2),
    "stock.max_qty": (5, 20, 5),
    "stock.goods_max_list": (20000, 1000000, 50000),   # D2 goods categories; big-ticket is stock.big_ticket_list
    "stock.cover_min": (20, 180, 15),
    "stock.cover_safety": (1.0, 3.0, 0.25),
    "stock.focus_cover_mult": (1.0, 3.0, 0.25),
    "stock.focus_cap_share": (0.3, 0.8, 0.1),
    "stock.focus_max": (4, 12, 2),              # lo is at least the current focus list's length
    "stock.max_hold": (20, 80, 10),
    "stock.min_line_qty": (1, 10, 1),
    "stock.trend_max": (0, 32, 4),              # lo is at least the current trend list's length
    "stock.trend_share": (0.03, 0.20, 0.02),
    "stock.min_margin_share": (0.02, 0.12, 0.01),
    "stock.big_ticket_share": (0.10, 0.90, 0.10),
    "stock.big_ticket_list": (20000, 200000, 10000),
    "stock.bigticket_probe_qty": (1, 10, 1),
    "stock.demand_share_max": (0.10, 0.80, 0.10),    # memory/demand: cap on our share of a SKU's market rate
    "stock.analyst_mult_max": (1.0, 2.0, 0.25),      # strategist/analyst.py: 1.0 switches its multipliers off
    # end of round (Patch 3.3: stock counts at cost, so 0 = off is the default)
    "stock.clear_cost_h": (0, 72, 12),
    "stock.clear_70_h": (0, 48, 6),
    "stock.clear_any_h": (0, 24, 3),
    "stock.taper_h": (0, 96, 12),
    "stock.goods_stop_h": (0, 48, 6),
    "buyer.cap_goods_no_warranty": (0.55, 0.85, 0.05),
    "buyer.cap_goods_warranty": (0.80, 1.00, 0.04),
    "buyer.cap_food": (0.85, 1.00, 0.03),
    "buyer.min_surplus_list": (0.0, 0.15, 0.02),
    "buyer.min_kept": (0.0, 0.45, 0.03),           # Patch 3.4: Saved is an average share kept; keep this at or above the board's saved
    "buyer.open_list": (0.20, 0.80, 0.05),
    "buyer.max_shops": (1, 5, 1),
    "buyer.target_s": (10, 40, 5),
    "buyer.continue_p": (0.5, 0.95, 0.05),
}

# key -> the strategist/facts.py fact it is set to, only by rows with source "market-rule"
# (surge_*: /api/supply-prices free, per_unit, max; facts.sync_proposals)
SYNCED = {"forum.min_gap_min": "post_gap_min", "market.surge_free": "surge_free",
          "market.surge_per_unit": "surge_per_unit", "market.surge_max": "surge_max"}
# no "bounds" rows for these: the buyer works well as it is, its keys move only by the model or a revert
NO_BOUNDS = ("buyer.",)
MAX_LISTS = 40          # focus_max + trend_max: about 48 supply lines an hour

# name -> (keys, how far the policy is past the constraint; 0 when it holds)
LINKED = {
    "buyer.cap_food + buyer.min_surplus_list <= 1 (above 1 the food cap is 1 - min_surplus_list, "
    "so raising cap_food does nothing)":
        (("buyer.cap_food", "buyer.min_surplus_list"),
         lambda p: max(0.0, p["buyer"]["cap_food"] + p["buyer"]["min_surplus_list"] - 1)),
    "stock.min_line_qty <= stock.max_qty (a smaller line is skipped, so a larger minimum blocks every top-up)":
        (("stock.min_line_qty", "stock.max_qty"), lambda p: max(0, p["stock"]["min_line_qty"] - p["stock"]["max_qty"])),
    "stock.max_qty <= stock.max_hold (a line never buys more than a SKU may hold)":
        (("stock.max_qty", "stock.max_hold"), lambda p: max(0, p["stock"]["max_qty"] - p["stock"]["max_hold"])),
    f"stock.focus_max + stock.trend_max <= {MAX_LISTS} (about 48 supply lines an hour)":
        (("stock.focus_max", "stock.trend_max"),
         lambda p: max(0, p["stock"]["focus_max"] + p["stock"]["trend_max"] - MAX_LISTS)),
}
EPS = 1e-9
SOURCES = ("model", "market-rule", "revert", "bounds", "migration", "linked", "objective")
OBJECTIVE_KEYS = ("buyer.min_kept",)    # the only keys a source "objective" row (strategist/objective.py) may set
LINKED_REPAIR = ("buyer.cap_food",)     # the only key a "linked" row may set (linked_rows)


def bounds(key, pol=None):
    """(lo, hi, max_step) for a tunable key, narrowed by policy.RANGES. With pol, stock.focus_max and
    stock.trend_max cannot go below the length of their list (validate would reject the whole set)."""
    lo, hi, step = TUNABLE[key]
    if key in policy.RANGES:
        rlo, rhi = policy.RANGES[key]
        lo, hi = max(lo, rlo), min(hi, rhi)
    if key in ("stock.focus_max", "stock.trend_max") and pol:
        skus = _get(pol, key.replace("_max", "_skus"))
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


def value(pol, key):
    """The key's value in pol, or its policy.DEFAULTS value when the file does not have it (the shop runs on it)."""
    v = _get(pol, key)
    return _get(policy.DEFAULTS, key) if v is None else v


def tunable_view(pol):
    """The table the prompt shows: current value and limits of every tunable key."""
    rows = []
    for key in TUNABLE:
        lo, hi, step = bounds(key, pol)
        value_ = value(pol, key)
        rows.append({"key": key, "value": value_, "lo": lo, "hi": hi, "max_step": step})
    return rows


def bounds_rows(pol):
    """A source "bounds" row for every tunable key (not NO_BOUNDS) whose value is outside its bounds: to the
    nearest bound. E.g. a floor set under wider bounds before they were narrowed."""
    rows = []
    for key in TUNABLE:
        v = value(pol, key)
        if key.startswith(NO_BOUNDS) or not _number(v):
            continue
        lo, hi, _ = bounds(key, pol)
        if not lo <= v <= hi:
            rows.append({"key": key, "value": min(max(v, lo), hi), "source": "bounds",
                         "reason": f"{v} outside the tunable range [{lo}, {hi}]"})
    return rows


def linked_rows(pol):
    """A source "linked" row setting buyer.cap_food to 1 - buyer.min_surplus_list (within its bounds) when their
    sum is above 1. The buyer already caps food at min(cap_food, 1 - min_surplus_list) (tools/buyer_policy.py
    cap_share), so its food cap stays the same; only the linked alert, which wakes the model every run, goes. None
    when the guard's rounding would move the value (a min_surplus_list with more than 4 decimals)."""
    cap, surplus = value(pol, "buyer.cap_food"), value(pol, "buyer.min_surplus_list")
    if not _number(cap) or not _number(surplus) or cap + surplus - 1 <= EPS:
        return []
    lo, hi, _ = bounds("buyer.cap_food", pol)
    new = min(max(round(1 - surplus, 6), lo), hi)
    if _round("buyer.cap_food", new) != new:
        return []
    return [{"key": "buyer.cap_food", "value": new, "source": "linked",
             "reason": f"cap_food {cap} + min_surplus_list {surplus} > 1: the buyer caps food at "
                       f"{round(1 - surplus, 6)} already"}]


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


def linked_alerts(pol, rows=()):
    """Names of the LINKED constraints the policy breaks now, or once rows (proposal rows: key, value) are set."""
    out = []
    if rows:
        pol = copy.deepcopy(pol)
        for r in rows:
            _set(pol, r["key"], r["value"])
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


def guard(proposals, pol, last_change_ts, now, max_changes=3, cooldown_h=3, last=None, blocked=None, p_clear=None):
    """(accepted, rejected). accepted rows: key, old, new, requested, clamped, reason, source.
    rejected rows: key, requested, why. last: {key: {"new": value the strategist last wrote, ...}}, so a value
    it wrote that is now outside the range may move back in. p_clear: the market's 24 h median paid/list of goods
    (None: unknown, no floor check). A validate failure rejects every accepted row, except that the rows of other
    sources than "model" that pass on their own are kept."""
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
        if source == "bounds" and key.startswith(NO_BOUNDS):
            reject("no bounds repair for this key")
            continue
        if source == "linked" and key not in LINKED_REPAIR:
            reject("no linked repair for this key")
            continue
        if source == "objective" and key not in OBJECTIVE_KEYS:
            reject("not an objective key")
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
        old = value(pol, key)
        if old is None:
            reject("not in policy")
            continue
        if not _number(old):
            reject("current value is not a number")
            continue
        if source == "market-rule":
            new, clamped = _sync(key, requested)
        elif source == "bounds":
            lo, hi, _ = bounds(key, pol)
            if lo <= old <= hi:
                reject("inside its bounds")
                continue
            new = _round(key, min(max(requested, lo), hi))
            clamped = new != requested
        elif source in ("migration", "linked", "objective"):
            lo, hi, _ = bounds(key, pol)
            new = _round(key, min(max(requested, lo), hi))
            clamped = new != requested
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
        if source == "model" and key == "seller.floor_list" and new > old and _number(p_clear) and new > p_clear:
            reject(f"floor raise above the market's 24 h goods clearing price {p_clear} of list")
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
