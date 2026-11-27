"""Public-snapshot stock ratio and cache resolution; no fetching or file I/O.

Restock supplies its live constants and clock value to preserve its patchable API.
Inventory depth, buying decisions and hub persistence remain with restock.
"""
import math
import statistics


def market_ratio(shops, board, catalog, *, default_ratio, ratio_min, ratio_max):
    """Pure calculation from full public API snapshots, never order-specific files."""
    shops = shops.get("shops", []) if isinstance(shops, dict) else shops
    board = board.get("shops", []) if isinstance(board, dict) else board
    metrics = {s.get("name"): s for s in board if isinstance(s, dict)}
    ratios = []
    for shop in shops:
        if not isinstance(shop, dict):
            continue
        data = {**shop, **metrics.get(shop.get("name"), {})}
        revenue, profit, stock = data.get("revenue"), data.get("profit"), shop.get("stock")
        if (not isinstance(revenue, (int, float)) or not math.isfinite(revenue)
                or not isinstance(profit, (int, float)) or not math.isfinite(profit)
                or not isinstance(stock, dict)):
            continue
        if any(s not in catalog or not isinstance(q, (int, float)) or not math.isfinite(q) or q < 0
               for s, q in stock.items()):
            continue
        estimated = sum(q * int(catalog[s].get("list_price", 0)) * .75 for s, q in stock.items())
        cash = 5_000_000 + profit
        if cash < 0 or cash + estimated <= 0:
            continue
        ratios.append((revenue, str(shop.get("name", "")), estimated / (cash + estimated)))
    if len(ratios) < 3:
        return default_ratio, {"source": "default: insufficient public shops/financials", "default": True,
                               "shops": len(ratios)}
    top = sorted(ratios, key=lambda r: (-r[0], r[1]))[:5]
    return max(ratio_min, min(ratio_max, statistics.median(r[2] for r in top))), {
        "source": "/api/shops+/api/board: top revenue median", "default": False, "shops": len(top)}


def resolve_market_ratio(hub, observation, *, now, default_ratio, ratio_min, ratio_max, ratio_cache_s):
    if observation is not None and not observation[1]["default"]:
        ratio, source = observation
        hub["market_ratio_cache"] = {"ratio": ratio, "ts": now, "shops": source["shops"], "source": source["source"]}
        return ratio, {**source, "cached": False, "ts": now}
    cached = hub.get("market_ratio_cache", {})
    ratio, ts = cached.get("ratio"), cached.get("ts")
    if (isinstance(ratio, (int, float)) and ratio_min <= ratio <= ratio_max
            and isinstance(ts, (int, float)) and 0 <= now - ts <= ratio_cache_s):
        return ratio, {"source": cached["source"], "shops": cached["shops"], "ts": ts,
                       "default": False, "cached": True, "refresh_failed": True}
    return default_ratio, {"source": "default: public API unavailable/insufficient, no fresh cache",
                           "default": True, "cached": False, "shops": 0}
