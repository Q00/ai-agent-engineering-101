"""Policy scheduling from observed outcomes and current R3 rank opportunities."""
import time
import math
from bisect import bisect_right

WEIGHTS = {'shop': {'book': .5, 'revenue': .3, 'stars_rank': .2},
           'buyer': {'surplus': .5, 'forum': .3, 'deals': .1, 'posts': .1}}


def eligible_rows(board, role, own_name):
    """Use the published trading cohort; alive is deliberately not a filter.

    The public board omits a shop's supply count. A nonzero profit, even with
    zero sales, is evidence of a purchase/storage charge. This is a proxy for
    that unpublished eligibility field, not a claim to reproduce official ALL.
    """
    if not board or board.get('round') != 3 or role not in WEIGHTS:
        return []
    rows = board.get('shops' if role == 'shop' else 'buyers', [])
    from .finance import book_row
    rows = [dict(r, book=book_row(r)) for r in rows] if role == 'shop' else rows
    return [r for r in rows if r.get('name') == own_name or r.get('deals', 0) > 0 or
            role == 'shop' and (r.get('profit', 0) != 0 or r.get('revenue', 0) > 0)]


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def interpolated_percentile(values, value):
    """A continuous planning surrogate between observed rank thresholds.

    The current own observation stays an anchor. Small profitable actions can
    therefore make progress even before passing a peer. This is not official ALL.
    """
    anchors = sorted(set(values))
    if len(anchors) < 2:
        return percentile(values, value)
    if value <= anchors[0]:
        return percentile(values, anchors[0])
    if value >= anchors[-1]:
        return percentile(values, anchors[-1])
    right = bisect_right(anchors, value)
    low, high = anchors[right - 1:right + 1]
    fraction = (value - low) / (high - low)
    return percentile(values, low) + fraction * (percentile(values, high) - percentile(values, low))


def rank_progress(board, role, own_name, delta):
    rows = eligible_rows(board, role, own_name)
    own = next((r for r in rows if r.get('name') == own_name), None)
    if own is None:
        return None
    gain = 0
    for key, weight in WEIGHTS[role].items():
        if not finite(own.get(key)) or not finite(delta.get(key, 0)):
            continue
        values = [r[key] for r in rows if finite(r.get(key))]
        gain += weight * (interpolated_percentile(values, own[key] + delta.get(key, 0)) -
                          interpolated_percentile(values, own[key]))
    return gain


def rank_targets(board, role, own_name='seochanit'):
    rows = eligible_rows(board, role, own_name)
    own = next((r for r in rows if r.get('name') == own_name), None)
    if own is None:
        return {}
    result = {}
    for key, weight in WEIGHTS[role].items():
        if not finite(own.get(key)):
            continue
        ahead = sorted(r[key] for r in rows if finite(r.get(key)) and r[key] > own[key])
        result[key] = {'current': own[key], 'next_value': ahead[0] if ahead else None,
                       'gap': ahead[0] - own[key] if ahead else None, 'weight': weight,
                       'cohort_size': sum(finite(r.get(key)) for r in rows)}
    return result


def percentile(values, value):
    """Midrank empirical approximation; not the unpublished official tie rule."""
    if not values:
        return 0.5
    return (sum(v < value for v in values) + .5 * sum(v == value for v in values)) / len(values)


def rank_gain(board, role, own_name, delta):
    rows = eligible_rows(board, role, own_name)
    own = next((x for x in rows if x.get("name") == own_name), None)
    if not own:
        return None
    weights = WEIGHTS[role]
    gain = 0
    for key, w in weights.items():
        values = [r[key] for r in rows if r.get('name') != own_name and isinstance(r.get(key), (int, float))]
        if values and isinstance(own.get(key), (int, float)):
            updated = own[key] + delta.get(key, 0)
            gain += w * (percentile(values + [updated], updated) - percentile(values + [own[key]], own[key]))
    return gain


def quote_completion(journal, sku, price, now=None):
    """Observe paid task outcomes and matured uncompleted opportunities, not imagined rejections."""
    now = time.time() if now is None else now
    paid = {x.get("task") for x in journal.events("shop_settled", 10000)}
    comparable = {}
    for e in journal.events("shop_quote", 10000):
        if e.get("sku") == sku and abs(e["price"] - price) <= max(1, price * .1):
            comparable.setdefault(e["task"], e)
    known = [e for task, e in comparable.items() if task in paid or now - e["_ts"] >= 300]
    successes = sum(e["task"] in paid for e in known)
    return {"samples": len(known), "paid": successes, "completion": (successes + 1) / (len(known) + 2),
            "unpaid_at_horizon_is_not_proven_rejection": True}


def observed_breaks(journal, sku):
    purchases = {x["order"]: x for x in journal.events("purchase", 10000) if x.get("sku") == sku}
    latest = {}
    for e in journal.events("outcome", 10000):
        # A verified broken revision supersedes an earlier provisional non-broken outcome.
        if e.get("order") in purchases and (e.get("broken") or e.get("order") not in latest):
            latest[e["order"]] = e
    known = [e for e in latest.values() if e.get("broken") or e.get("matured")]
    return {"mature_samples": len(known), "broken": sum(bool(e.get("broken")) for e in known)}
