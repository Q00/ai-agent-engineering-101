"""Check explicit request attributes before exposing retrieved candidates."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from market_a2a.models import Product

FEATURES: Final = (
    "lockable",
    "flexible",
    "dimmable",
    "rounded",
    "mesh",
    "lumbar",
    "armrests",
    "cable tray",
    "dual monitor",
)


def compatible(product: Product, request: str) -> bool:
    """Guard named attributes without using any evaluation SKU oracle."""
    text = request.lower()
    description = product.description.lower()
    if any(feature in text and feature not in description for feature in FEATURES):
        return False
    maximum = re.search(r"(?:no taller than|at most)\s+(\d+)\s*cm", text)
    if maximum and product.height_cm > int(maximum[1]):
        return False
    capacity = re.search(r"(?:at least|about|for)\s+(\d+)\s*(?:litres?|books)", text)
    return not capacity or product.capacity >= int(capacity[1])
