"""Which unit of a SKU the seller offers first, read from sale_order.json on every request. A missing or broken setting means oldest first, as before."""

import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "sale_order.json"


def grade_c_last(path=None):
    try:
        return json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8")).get("grade_c_last") is True
    except (OSError, ValueError, AttributeError):
        return False


def order_key(unit, c_last):
    """Food that expires first still goes first. Among units with the same expiry, goods of grade C wait behind A and B when c_last, then oldest first."""
    return (unit["expires_at"] or float("inf"), bool(c_last and unit.get("kind") == "goods" and unit.get("grade") == "C"), unit["supplied_at"])
