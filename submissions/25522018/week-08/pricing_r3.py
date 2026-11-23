
"""Conservative, grade-aware Round 3 seller pricing."""
from math import ceil
import time

GRADE_FACTOR = {
    "A": 1.00,
    "B": 0.93,
    "C": 0.82,
    "D": 0.72,
}

def price_quote(unit):
    cost = max(0.0, float(unit.get("cost") or 0))
    list_price = max(cost, float(unit.get("list_price") or cost))
    grade = str(unit.get("grade") or "B").upper()

    # Target an 8% gross margin above procurement cost.
    reserve = ceil(cost * 1.08)

    # Lower the initial asking price for lower-grade goods.
    target = ceil(list_price * GRADE_FACTOR.get(grade, 0.85))

    # Discount short-lived inventory without crossing the reserve.
    expires = unit.get("expires_at")
    if isinstance(expires, (int, float)) and expires > 0:
        hours_left = (expires - time.time()) / 3600
        if 0 < hours_left < 24:
            target = ceil(target * 0.92)

    return reserve, max(reserve, target)
