"""score_twin: the objective layer for the buyer's Saved part, run by code every strategist run.

Saved (the market's rule, checked each run as the presence fact saved_is_kept_share_over_deals_plus_100) is the
sum over deals of surplus / list, divided by deals + 100: a mean share kept per deal. A deal that keeps less than
the current mean lowers it, so buyer.min_kept (the kept floor every buyer cap respects, tools/buyer_policy.py)
should sit above the board's saved. This module reads our saved and the saved of the ASPIRE_RANK-th buyer from
the board, sets the target max(ours, theirs) + MARGIN, and moves buyer.min_kept toward it one guard step a run,
raising only while the buyer's conversion (window orders -> deals; the board's cumulative one under MIN_ORDERS)
is at least CONV_RAISE and lowering a step (never under the target) when it falls under CONV_LOWER: deals weigh
0.1 of buyer ALL and saved 0.5, so a few lost deals are worth a higher mean. The rows carry source "objective";
guard.py applies them within the key's bounds without cooldown or max_changes. When the Saved sentence is not the
one this layer was built on (the fact is None), it proposes nothing and alerts the model, which then holds the key.
"""
import math

KEY = "buyer.min_kept"
RULE_FACT = "saved_is_kept_share_over_deals_plus_100"
MARGIN = 0.02               # target = max(our saved, the aspiration) + this
ASPIRE_RANK = 10            # the saved of this rank among ranked buyers (deals > 0, not house) is the aspiration
CONV_RAISE = 0.45           # window conversion at or above: a raise is allowed
CONV_LOWER = 0.30           # under: one step down (never under the target)
MIN_ORDERS = 20             # window orders for the window conversion; fewer: the board's cumulative one
GRID = 0.01                 # the target is rounded up to this


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _d(x):
    return x if isinstance(x, dict) else {}


def aspiration(buyers, me, rank=ASPIRE_RANK):
    """The saved of the rank-th ranked buyer (house rows and buyers without a deal left out, ours included),
    or None under rank rows."""
    vals = []
    for r in buyers if isinstance(buyers, list) else []:
        if not isinstance(r, dict) or r.get("house") or (_num(r.get("deals")) or 0) <= 0:
            continue
        v = _num(r.get("saved"))
        if v is not None:
            vals.append(v)
    vals.sort(reverse=True)
    return vals[rank - 1] if len(vals) >= rank else None


def conversion(brief):
    """(conversion, source): the window's buyer_log orders -> deals over MIN_ORDERS, else the board's."""
    bl = _d(_d(brief).get("buyer_log"))
    orders = sum(_num(_d(bl.get(k)).get("orders")) or 0 for k in ("food", "goods"))
    deals = sum(_num(_d(bl.get(k)).get("deals")) or 0 for k in ("food", "goods"))
    if orders >= MIN_ORDERS:
        return round(deals / orders, 4), "window"
    c = _num(_d(_d(_d(brief).get("me")).get("buyer")).get("conversion"))
    return c, "board" if c is not None else None


def status(brief, buyers, pol, facts, me):
    """What the layer sees and would do: {saved, aspire, target, conv, conv_src, current, lo, hi, step, action,
    why}. action: raise, lower, hold or paused."""
    from strategist import guard
    saved = _num(_d(_d(_d(brief).get("me")).get("buyer")).get("saved"))
    lo, hi, step = guard.bounds(KEY, pol)
    cur = guard.value(pol, KEY)
    conv, src = conversion(brief)
    asp = aspiration(buyers, me)
    out = {"saved": saved, "aspire": asp, "target": None, "conv": conv, "conv_src": src, "current": cur,
           "lo": lo, "hi": hi, "step": step, "action": "hold", "why": ""}
    if _d(facts).get(RULE_FACT) is not True:
        out.update(action="paused", why="the market's Saved sentence is not the one score_twin was built on")
        return out
    if saved is None or not isinstance(cur, (int, float)):
        out.update(why="no saved on the board or no current value")
        return out
    base = max(saved, asp if asp is not None else saved)
    target = min(max(math.ceil((base + MARGIN) / GRID - 1e-9) * GRID, lo), hi)
    out["target"] = round(target, 4)
    if cur < target - 1e-9:
        if conv is None or conv >= CONV_RAISE:
            out.update(action="raise", value=round(min(cur + step, target), 4),
                       why=f"min_kept {cur} under target {out['target']} (saved {saved}, rank-{ASPIRE_RANK} saved {asp}), "
                           f"conversion {conv} ({src})")
        else:
            out["why"] = f"under target {out['target']} but conversion {conv} ({src}) under {CONV_RAISE}: held"
    elif cur > target + step - 1e-9 and conv is not None and conv < CONV_LOWER:
        out.update(action="lower", value=round(max(cur - step, target), 4),
                   why=f"conversion {conv} ({src}) under {CONV_LOWER} with min_kept {cur} a step over target {out['target']}")
    else:
        out["why"] = f"min_kept {cur} at target {out['target']} (saved {saved}, rank-{ASPIRE_RANK} saved {asp})"
    return out


def rows(brief, buyers, pol, facts, me):
    """(rows, alerts, status): at most one source "objective" row for buyer.min_kept."""
    st = status(brief, buyers, pol, facts, me)
    if st["action"] == "paused":
        return [], [f"score_twin paused: {st['why']} (fact {RULE_FACT} is not true): {KEY} is left to the model"], st
    if st["action"] in ("raise", "lower"):
        return [{"key": KEY, "value": st["value"], "source": "objective", "reason": f"score_twin {st['action']}: {st['why']}"[:200]}], [], st
    return [], [], st
