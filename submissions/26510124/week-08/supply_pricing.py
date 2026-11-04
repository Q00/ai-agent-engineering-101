"""Conservative supply exposure; public surge is a filter, never a quote lock."""
from feedback import number


def worst_cost(list_price):
    """Unknown grade <=75% of list, plus the documented maximum 50% surge."""
    if type(list_price) is not int or list_price <= 0:
        raise ValueError("Invalid list price")
    base = (list_price * 75 + 99) // 100
    return (base * 150 + 99) // 100 + 1


def surge_factors(inventory):
    """The kit supplies a complete list of currently surcharged SKUs.

    A valid empty list means no surcharge. Missing/malformed metadata is
    unknown, not a free-price quote. Accept the public endpoint wrapper too.
    """
    raw = inventory.get("surge")
    if isinstance(raw, dict):
        raw = raw.get("surge")
    if not isinstance(raw, list) or len(raw) > 20000:
        return None
    factors = {}
    for row in raw:
        if (not isinstance(row, dict) or not isinstance(row.get("sku"), str)
                or not row["sku"] or row["sku"] in factors
                or not number(row.get("factor")) or not 1 <= row["factor"] <= 1.5
                or type(row.get("units")) is not int or row["units"] < 0):
            return None
        factors[row["sku"]] = float(row["factor"])
    return factors
