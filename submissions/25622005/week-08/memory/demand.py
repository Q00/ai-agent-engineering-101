"""The demand model behind restock's cover target: what we can expect to sell of a SKU until the next supply
window, from the public ledger and our own shelf history, without an LLM.

Three corrections to the plain "our sales per hour over 24 h":
  1. stock-out censoring: our rate counts only the hours a SKU was on our shelf (memory/instock.json, written by
     the restock hook every run: minutes in stock per SKU per hour; memory/ingest keeps our settles per SKU per
     hour as market.own_sku_hour), so a SKU that sold 9 in the 3 hours it was in stock reads as 3 an hour, not 0.4;
  2. market share: our settles / the market's settles of the SKU in the hours we were in stock (market.sku_hour_24),
     times the market's current rate: the same number from the other side, for SKUs the market moves faster than
     our shelf shows; the share is capped at stock.demand_share_max;
  3. the hour of day: the market settles 2.2x more at 03-09 h than at 11-23 h (market.hour_profile, mean settles
     per hour of day over the folded buckets), so the rate for the coming cover window is scaled by the profile
     of those hours against the day's mean.
forecast() returns the higher of (1) and (2), scaled by (3), or None when neither has enough data (then the
planner keeps its old rate). Everything here is read-only except tick_instock, which writes memory/instock.json.
"""
import time

MIN_INSTOCK_H = 2.0          # hours in stock before the in-stock rate counts
MIN_INSTOCK_MIN = 20         # minutes of an hour in stock for that hour to count in the share
MIN_MARKET_N = 10            # market settles over the counted hours before the share counts
MIN_PROFILE_H = 12           # hours of day with data before the hour factor applies
FACTOR_BOUNDS = (0.5, 2.0)
TICK_MAX_S = 300             # a gap between restock runs longer than this counts as this
KEEP_H = 48
LOOKBACK_H = 24


def _d(x):
    return x if isinstance(x, dict) else {}


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _hours(now, back=LOOKBACK_H):
    """The hour keys (epoch hour as str) of the last `back` hours, the current one included."""
    h = int(now // 3600)
    return [str(x) for x in range(h - back + 1, h + 1)]


# --- the shelf history (restock hook, each run)

def tick_instock(data, inv, now, keep_h=KEEP_H):
    """Add the minutes since the last tick (at most TICK_MAX_S) to every SKU with a live unit in inv; prune hours
    older than keep_h. Returns the updated dict ({"v", "last", "hours": {hour: {sku: minutes}}})."""
    st = data if isinstance(data, dict) and isinstance(data.get("hours"), dict) else {"v": 1, "last": None, "hours": {}}
    last = _num(st.get("last"))
    minutes = (min(TICK_MAX_S, max(0.0, now - last)) if last is not None else 60.0) / 60
    skus = {u.get("sku") for u in _d(inv).get("units") or [] if isinstance(u, dict) and isinstance(u.get("sku"), str)
            and u.get("grade") != "expired"}
    hour = str(int(now // 3600))
    row = st["hours"].setdefault(hour, {})
    for sku in skus:
        row[sku] = round(min(60.0, (row.get(sku) or 0) + minutes), 2)
    cut = int(now // 3600) - keep_h
    for h in [h for h in st["hours"] if not str(h).isdigit() or int(h) < cut]:
        del st["hours"][h]
    st["last"] = now
    return st


# --- the three estimates

def hour_profile(summary):
    """{"HH": mean settles per hour of day} from the summary (memory/ingest.render_market), or {}."""
    p = _d(_d(_d(summary).get("market")).get("hour_profile"))
    return {k: v for k, v in p.items() if isinstance(k, str) and _num(v) is not None}


def hour_factor(summary, t0, horizon_h):
    """The market's activity in the hours [t0, t0 + horizon_h) against its daily mean, within FACTOR_BOUNDS; None
    without MIN_PROFILE_H hours of profile."""
    p = hour_profile(summary)
    if len(p) < MIN_PROFILE_H:
        return None
    mean = sum(p.values()) / len(p)
    if mean <= 0:
        return None
    hours, t = [], t0
    while t < t0 + horizon_h * 3600 - 1e-9:
        hours.append(time.strftime("%H", time.localtime(t)))
        t += 3600
    vals = [p[h] for h in hours if h in p]
    if not vals:
        return None
    return round(min(FACTOR_BOUNDS[1], max(FACTOR_BOUNDS[0], sum(vals) / len(vals) / mean)), 3)


def instock_stats(summary, instock, sku, now):
    """(our settles, hours in stock, market settles in the hours we were in stock for at least MIN_INSTOCK_MIN
    minutes) over the last LOOKBACK_H hours."""
    own = _d(_d(_d(_d(summary).get("market")).get("own_sku_hour")).get(sku))
    mkt = _d(_d(_d(_d(summary).get("market")).get("sku_hour_24")).get(sku))
    hours = _d(_d(instock).get("hours"))
    sold, h_in, m_in = 0, 0.0, 0
    for h in _hours(now):
        mins = _num(_d(hours.get(h)).get(sku)) or 0
        if mins <= 0:
            continue
        h_in += mins / 60
        sold += int(_num(own.get(h)) or 0)
        if mins >= MIN_INSTOCK_MIN:
            m_in += int(_num(mkt.get(h)) or 0)
    return sold, round(h_in, 3), m_in


def forecast(summary, instock, sku, now, horizon_h, pol):
    """{"rate": our expected sales per hour over the next horizon_h hours or None, "src": "instock" | "share" |
    None, "instock_h", "sold", "share", "factor", "market_rate"}. rate = max(in-stock rate, min(share,
    stock.demand_share_max) x the market's 6 h rate) x the hour factor."""
    st = _d(_d(pol).get("stock"))
    sold, h_in, m_in = instock_stats(summary, instock, sku, now)
    m_rate = _num(_d(_d(_d(summary).get("market")).get("sku_rate")).get(sku))
    out = {"rate": None, "src": None, "instock_h": h_in, "sold": sold, "share": None, "factor": None, "market_rate": m_rate}
    cands = []
    if h_in >= MIN_INSTOCK_H:
        cands.append((sold / h_in, "instock"))
    if m_in >= MIN_MARKET_N and m_rate is not None:
        share = min(sold / m_in, _num(st.get("demand_share_max")) or 1.0)
        out["share"] = round(share, 4)
        cands.append((share * m_rate, "share"))
    if not cands:
        return out
    rate, src = max(cands)
    f = hour_factor(summary, now, horizon_h)
    out["factor"] = f
    out.update(rate=round(rate * (f if f is not None else 1.0), 4), src=src)
    return out
