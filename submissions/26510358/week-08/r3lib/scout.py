"""Competitor scout: what other shops are buying, from the public ledger's `supply` entries.

The ledger publishes every supply (shop, sku, qty, total). On 2026-11-04 no other shop's card or post used it.
Compared with what the market settles in the same window, it tells whether a category is being over- or
under-supplied before the stock shows up in /api/shops:

  pressure = units competitors supplied / units the market settled   (same window, same category)

pressure < 1: the market sells more than competitors restock (a gap: stock deeper, ask a little higher);
pressure > 1: competitors are piling in (stock shallower). Our own realised share of a category's settles is a
second, direct estimate of the share the restock plan otherwise guesses as 1 / (1 + shops holding stock).
"""
import time

from . import config, market

_CACHE = {}
CACHE_S = 60


def category_view(mem, hours=6, now=None):
    """category -> {sold, supplied, pressure, our_sold, our_share}. Cached for CACHE_S (the shop calls it per talk)."""
    now = now or time.time()
    key = (id(mem), hours)
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    cat = market.catalog()
    since = now - hours * 3600
    out = {}
    mine = mem.my_shop_urls()
    with mem.db() as c:
        for r in c.execute("SELECT category, shop, COUNT(*) n FROM settles WHERE ts>? AND category IS NOT NULL "
                           "GROUP BY category, shop", (since,)):
            d = out.setdefault(r["category"], {"sold": 0, "supplied": 0, "our_sold": 0})
            d["sold"] += r["n"]
            if r["shop"] in mine:
                d["our_sold"] += r["n"]
        for r in c.execute("SELECT json_extract(data, '$.shop') shop, json_extract(data, '$.sku') sku, "
                           "SUM(json_extract(data, '$.qty')) q FROM ledger WHERE kind='supply' AND ts>? "
                           "GROUP BY shop, sku", (since,)):
            if r["shop"] in config.MY_NAMES:
                continue
            k = cat.get(r["sku"], {}).get("category")
            if k:
                out.setdefault(k, {"sold": 0, "supplied": 0, "our_sold": 0})["supplied"] += r["q"] or 0
    for d in out.values():
        d["pressure"] = round(d["supplied"] / d["sold"], 2) if d["sold"] else None
        d["our_share"] = round(d["our_sold"] / d["sold"], 3) if d["sold"] else 0.0
    _CACHE[key] = (now, out)
    return out


def gap_factor(view_row):
    """Scaling of a category's demand target from competitor pressure, clamped to [GAP_MIN, GAP_MAX].
    Needs SCOUT_MIN_SOLD settles in the window; otherwise 1.0 (no opinion)."""
    if not view_row or (view_row.get("sold") or 0) < config.SCOUT_MIN_SOLD or view_row.get("pressure") is None:
        return 1.0
    p = view_row["pressure"]
    if p <= config.SCOUT_LOW_PRESSURE:
        return config.GAP_MAX
    if p >= config.SCOUT_HIGH_PRESSURE:
        return config.GAP_MIN
    span = config.SCOUT_HIGH_PRESSURE - config.SCOUT_LOW_PRESSURE
    return round(config.GAP_MAX - (p - config.SCOUT_LOW_PRESSURE) / span * (config.GAP_MAX - config.GAP_MIN), 3)


def share_estimate(prior_share, view_row):
    """Share of a category's orders we expect: the 1/(1+shops) prior, or our realised share when it is higher
    and rests on at least SCOUT_MIN_OUR_SOLD of our own settles."""
    if view_row and (view_row.get("our_sold") or 0) >= config.SCOUT_MIN_OUR_SOLD:
        return max(prior_share, view_row["our_share"])
    return prior_share


def ask_premium(mem, category, now=None):
    """Extra share of list price on our first ask when the category is under-supplied and few competitors hold it.
    Small on purpose: stars (0.2 of shop ALL) and close rate matter more than a few percent of price."""
    if not category:
        return 0.0
    row = category_view(mem, now=now).get(category)
    if not row or (row.get("sold") or 0) < config.SCOUT_MIN_SOLD or row.get("pressure") is None:
        return 0.0
    return config.SCARCITY_PREMIUM if row["pressure"] <= config.SCOUT_LOW_PRESSURE else 0.0


def gaps(mem, k=10, now=None):
    """Most under-supplied categories with enough sales: [(leaf, sold, supplied, pressure)]."""
    rows = [(market.leaf(c), d["sold"], d["supplied"], d["pressure"]) for c, d in category_view(mem, now=now).items()
            if d["sold"] >= config.SCOUT_MIN_SOLD and d["pressure"] is not None]
    return sorted(rows, key=lambda r: r[3])[:k]


def tool(mem):
    from . import react
    return react.Tool("scout", "competitor supply vs market sales per category over 6 h (pressure < 1 means "
                      "under-supplied); lists the biggest gaps", lambda: {"gaps": gaps(mem)})
