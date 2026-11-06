"""What a receipt's `item` says. A buyer who asked for a category gets that category's name; everyone else gets the SKU.

The category is the unit's own, taken from our inventory and never from the buyer's words, so a receipt cannot call a unit something it is not. The checkout, the certificate and the
ledger still name the SKU. The setting is read for each deal; anything but an explicit true means the SKU.
"""

import json
from pathlib import Path
import re

CONFIG = Path(__file__).resolve().parent / "receipt_label.json"
ANY_SKU = re.compile(r"(?<![^\W_])g[0-9a-f]{8}(?![^\W_])")


def enabled(path=CONFIG):
    try:
        section = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(section, dict) and section.get("enabled") is True


def leaf(unit):
    return (unit.get("category") or "").split(">")[-1].strip()


def request_form(text, unit):
    """How the buyer named this unit: "sku", "category" or "other". A whole word only: 김치 is not asked for by 오이김치."""
    want = str(text or "").lower()
    if unit["sku"].lower() in want or ANY_SKU.search(want):
        return "sku"
    name = leaf(unit).lower()
    if name and re.search(r"(?<![^\W_])" + re.escape(name) + r"(?![^\W_])", want):
        return "category"
    return "other"


def receipt_item(text, unit, path=CONFIG):
    if unit.get("unit") and request_form(text, unit) == "category" and enabled(path):
        return leaf(unit)
    return unit["sku"]
