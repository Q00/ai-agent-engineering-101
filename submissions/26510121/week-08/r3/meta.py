"""Policy scheduling from observed outcomes and current R3 rank opportunities."""
import time


def percentile(values, value):
    """Midrank empirical approximation; not the unpublished official tie rule."""
    if not values:
        return 0.5
    return (sum(v < value for v in values) + .5 * sum(v == value for v in values)) / len(values)


def rank_gain(board, role, own_name, delta):
    if not board or board.get("round") != 3:
        return None
    rows = board.get("shops" if role == "shop" else "buyers", [])
    own = next((x for x in rows if x.get("name") == own_name), None)
    if not own:
        return None
    weights = {"profit": .5, "revenue": .3, "stars_rank": .2} if role == "shop" else {"surplus": .5, "forum": .3, "deals": .1, "posts": .1}
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
