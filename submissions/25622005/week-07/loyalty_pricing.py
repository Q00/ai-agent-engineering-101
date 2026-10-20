"""Purchase discounts based on completed sales under a client-supplied ID.

The ID is a compatibility feature, not authenticated buyer identity. Anonymous
requests receive the welcome offer; they cannot be linked across tasks.
"""
import re

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
BASE_PRICES = {"lamp": 110, "chair": 200, "bookshelf": 285}


def customer_id_from(metadata):
    value = metadata.get("customer_id") if isinstance(metadata, dict) else None
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,79}", value):
        return value
    return None


def purchase_quote(item, completed_purchases):
    """Round payment up to a whole number, then enforce the course floor."""
    number = completed_purchases + 1
    percent = {1: 10, 2: 5}.get(number, 0)
    base = BASE_PRICES[item]
    discounted = (base * (100 - percent) + 99) // 100
    price = max(RESERVE[item], discounted)
    return {"purchase_number": number, "base_price": base,
            "discount_percent": percent, "price": price,
            "floor_applied": price > discounted}
