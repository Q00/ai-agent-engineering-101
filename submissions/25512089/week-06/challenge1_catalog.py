from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path("challenge1_catalog.json")


def load_catalog() -> list[dict[str, Any]]:
    data = json.loads(
        CATALOG_PATH.read_text(encoding="utf-8")
    )

    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON list.")

    return data


def search_catalog(
    catalog: list[dict[str, Any]],
    criteria: dict[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """
    Narrow the full catalog using structured criteria
    produced by the model.

    Only matching products are returned to the model.
    """

    category = criteria.get("category")
    materials = criteria.get("materials") or []
    colors = criteria.get("colors") or []
    styles = criteria.get("styles") or []

    max_height_cm = criteria.get("max_height_cm")
    min_capacity = criteria.get("min_capacity")
    max_capacity = criteria.get("max_capacity")

    matches: list[dict[str, Any]] = []

    for product in catalog:

        if (
            category
            and product["category"] != category
        ):
            continue

        if (
            materials
            and product["material"] not in materials
        ):
            continue

        if (
            colors
            and product["color"] not in colors
        ):
            continue

        if (
            styles
            and product["style"] not in styles
        ):
            continue

        if (
            max_height_cm is not None
            and product["height_cm"] > max_height_cm
        ):
            continue

        capacity = product.get("capacity")

        if min_capacity is not None:
            if capacity is None:
                continue

            if capacity < min_capacity:
                continue

        if max_capacity is not None:
            if capacity is None:
                continue

            if capacity > max_capacity:
                continue

        matches.append(product)

    # Prefer cheaper valid products.
    matches.sort(
        key=lambda p: (
            int(p["reserve"]),
            str(p["sku"]),
        )
    )

    return matches[:limit]