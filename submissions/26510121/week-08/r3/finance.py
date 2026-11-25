"""Shared book accounting, rolling horizons and surge-aware capital limits."""
from datetime import datetime
import math


def book_row(row):
    if type(row.get('book')) in (int, float):
        return row['book']
    return row.get('profit', 0) + row.get('stock_cost', 0)


def planning_hours(config, now):
    hours = max(1, min(48, config.get('planning_horizon_hours', 6)))
    # A process shutdown setting is not evidence that the market ends then.
    if config.get('market_end_at'):
        hours = min(hours, max(0, (datetime.fromisoformat(config['market_end_at']).timestamp()-now)/3600))
    return hours


def reserve_days(config, now):
    if config.get('market_end_at'):
        return max(0, (datetime.fromisoformat(config['market_end_at']).timestamp()-now)/86400)
    return max(1, min(30, config.get('storage_reserve_hours', 48)/24))


def surge_factor(inv, sku):
    row = next((r for r in inv.get('surge', []) if r.get('sku') == sku), {})
    value = row.get('factor', 1)
    return min(1.5, max(1, value)) if type(value) in (int, float) and math.isfinite(value) else 1.5


def supply_bound(product, inv, sku):
    # Other buyers can increase the factor during the three-minute window.
    # Reserve the published absolute maximum, rather than a stale live factor.
    return math.ceil(.75 * product['list_price'] * 1.5)


def sale_delta(price, cost, completion=1, refund_probability=0, storage=0):
    revenue = completion * price * (1-refund_probability)
    return {'book': revenue-completion*cost-storage, 'revenue': revenue,
            'profit': revenue-storage}
