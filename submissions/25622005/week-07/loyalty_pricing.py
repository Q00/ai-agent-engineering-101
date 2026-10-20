"""Same lowest permitted price for every purchase.

The previous loyalty tiers were replaced at the user's request. Keep the
function interface so historical callers can supply a purchase count safely.
"""
import re

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}


def customer_id_from(metadata):
    value = metadata.get("customer_id") if isinstance(metadata, dict) else None
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,79}", value):
        return value
    return None


def purchase_quote(item, completed_purchases):
    """Purchase history never raises the advertised payable price."""
    return {"price": RESERVE[item], "policy": "same_lowest_for_everyone"}
