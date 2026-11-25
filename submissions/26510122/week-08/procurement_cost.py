"""Worst-case new-lot reservations, including the market's maximum supply surge."""
import math
BASE_MAX_COST_SHARE = .75
MAX_SUPPLY_SURGE = 1.5

def ceiling(product):
    price = product.get('list_price')
    if type(price) is not int or price <= 0:
        raise ValueError('invalid catalog list price')
    return math.ceil(price * BASE_MAX_COST_SHARE * MAX_SUPPLY_SURGE)


def estimate_unit_cost(product, surge_factor=1):
    """Planning estimate from published grade odds and midpoints, at observed surge."""
    if type(surge_factor) not in (int, float) or not math.isfinite(surge_factor):
        surge_factor = 1
    # Food: .25/.50/.25; goods: .70/.20/.10; grade midpoints: .65/.50/.35.
    average_share = .50 if product.get('kind') == 'food' else .59
    return round(product['list_price'] * average_share * min(MAX_SUPPLY_SURGE, max(1, surge_factor)))
