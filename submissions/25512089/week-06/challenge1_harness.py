from __future__ import annotations

import json
from typing import Any

from harness import _call_json


VALID_CATEGORIES = {
    "bookshelf",
    "chair",
    "lamp",
    "desk",
    "storage cabinet",
    "side table",
    "shoe rack",
    "plant stand",
    "utility cart",
    "bench",
}

VALID_MATERIALS = {
    "wood",
    "metal",
    "bamboo",
    "engineered wood",
}

VALID_COLORS = {
    "natural",
    "black",
    "white",
    "brown",
    "gray",
}

VALID_STYLES = {
    "modern",
    "classic",
    "minimal",
    "industrial",
}


def description_to_criteria(
    description: str,
) -> dict[str, Any]:
    """
    The harness interprets the buyer's natural-language
    request and turns it into structured catalog criteria.
    """

    system = """
You are the catalog-search decision harness for an A2A seller.

A buyer describes what they want without giving a product SKU.

Convert the request into search criteria.

Allowed categories:
bookshelf, chair, lamp, desk, storage cabinet,
side table, shoe rack, plant stand, utility cart, bench

Allowed materials:
wood, metal, bamboo, engineered wood

Allowed colors:
natural, black, white, brown, gray

Allowed styles:
modern, classic, minimal, industrial

Important interpretation rules:
- "wooden" normally means material "wood".
- "about 40 books" means capacity roughly 35 to 45.
- "not taller than a desk" means max_height_cm 85.
- Use null when the buyer did not specify a constraint.
- Do not invent color or style requirements.
- Return only constraints supported by the buyer's description.

Return ONLY one JSON object in exactly this shape:

{
  "category": STRING_OR_NULL,
  "materials": [],
  "colors": [],
  "styles": [],
  "max_height_cm": INTEGER_OR_NULL,
  "min_capacity": INTEGER_OR_NULL,
  "max_capacity": INTEGER_OR_NULL
}
""".strip()

    user = f"""
Buyer description:

{description}

Convert this request into catalog search criteria.
""".strip()

    last_result: dict[str, Any] | None = None

    for attempt in range(1, 4):
        result = _call_json(
            system,
            user,
            label="challenge1_search_criteria",
        )

        last_result = result

        try:
            category = result.get("category")

            if category is not None:
                category = str(category).lower()

                if category not in VALID_CATEGORIES:
                    raise ValueError(
                        f"invalid category: {category}"
                    )

            materials = [
                str(x).lower()
                for x in result.get(
                    "materials",
                    [],
                )
            ]

            colors = [
                str(x).lower()
                for x in result.get(
                    "colors",
                    [],
                )
            ]

            styles = [
                str(x).lower()
                for x in result.get(
                    "styles",
                    [],
                )
            ]

            if not set(materials) <= VALID_MATERIALS:
                raise ValueError(
                    f"invalid materials: {materials}"
                )

            if not set(colors) <= VALID_COLORS:
                raise ValueError(
                    f"invalid colors: {colors}"
                )

            if not set(styles) <= VALID_STYLES:
                raise ValueError(
                    f"invalid styles: {styles}"
                )

            def optional_int(name: str):
                value = result.get(name)

                if value is None:
                    return None

                return int(value)

            return {
                "category": category,
                "materials": materials,
                "colors": colors,
                "styles": styles,
                "max_height_cm": optional_int(
                    "max_height_cm"
                ),
                "min_capacity": optional_int(
                    "min_capacity"
                ),
                "max_capacity": optional_int(
                    "max_capacity"
                ),
            }

        except Exception as exc:
            print(
                "[CHALLENGE1 SEMANTIC RETRY] "
                f"criteria attempt={attempt}/3 "
                f"result={result} "
                f"error={exc}",
                flush=True,
            )

    raise RuntimeError(
        "Challenge 1 search criteria remained "
        f"invalid: {last_result}"
    )


def choose_product(
    *,
    buyer_description: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    The harness sees only the small candidate set.
    It chooses the product and opening asking price.
    """

    system = """
You are the product-selection and pricing harness
for an A2A seller.

The full catalog has thousands of products.
The code has already narrowed it to valid candidates.

You must choose the candidate that best satisfies
the buyer's description and choose its opening price.

Rules:
- Select only a SKU from the supplied candidate list.
- The opening price must never be below that
  candidate's reserve.
- Prefer the candidate that most closely matches
  the buyer's request.
- Choose a practical whole-number asking price.
- Leave some room for negotiation.
- You make both the product-selection and pricing
  decisions.

Return ONLY JSON:

{
  "action": "select",
  "sku": "SKU",
  "price": INTEGER
}
""".strip()

    compact_candidates = [
        {
            "sku": p["sku"],
            "name": p["name"],
            "description": p["description"],
            "reserve": p["reserve"],
            "category": p["category"],
            "material": p["material"],
            "color": p["color"],
            "style": p["style"],
            "height_cm": p["height_cm"],
            "capacity": p["capacity"],
        }
        for p in candidates
    ]

    user = f"""
Buyer description:
{buyer_description}

Candidates:
{json.dumps(
    compact_candidates,
    ensure_ascii=False,
    indent=2,
)}

Choose the best product and opening asking price.
""".strip()

    last_result: dict[str, Any] | None = None

    by_sku = {
        str(p["sku"]): p
        for p in candidates
    }

    for attempt in range(1, 4):
        result = _call_json(
            system,
            user,
            label="challenge1_product_selection",
        )

        last_result = result

        try:
            action = str(
                result.get("action", "")
            ).lower()

            if action != "select":
                raise ValueError(
                    "action must be select"
                )

            sku = str(result["sku"])

            if sku not in by_sku:
                raise ValueError(
                    f"model selected unknown SKU: {sku}"
                )

            product = by_sku[sku]
            reserve = int(product["reserve"])

            price = int(result["price"])

            # Hard safety boundary:
            # model decides price, but code prevents
            # selling/asking below reserve.
            price = max(
                reserve,
                price,
            )

            return {
                "action": "select",
                "sku": sku,
                "price": price,
                "product": product,
            }

        except Exception as exc:
            print(
                "[CHALLENGE1 SEMANTIC RETRY] "
                f"selection attempt={attempt}/3 "
                f"result={result} "
                f"error={exc}",
                flush=True,
            )

    raise RuntimeError(
        "Challenge 1 product selection remained "
        f"invalid: {last_result}"
    )