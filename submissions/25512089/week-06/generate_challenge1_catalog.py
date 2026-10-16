from __future__ import annotations

import json
import random
from pathlib import Path


random.seed(25512089)

OUTPUT = Path("challenge1_catalog.json")

CATEGORIES = [
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
]

MATERIALS = [
    "wood",
    "metal",
    "bamboo",
    "engineered wood",
]

COLORS = [
    "natural",
    "black",
    "white",
    "brown",
    "gray",
]

STYLES = [
    "modern",
    "classic",
    "minimal",
    "industrial",
]

HEIGHTS = [
    55,
    65,
    75,
    85,
    95,
    110,
    130,
    150,
]

BOOK_CAPACITIES = [
    20,
    30,
    40,
    50,
    60,
    80,
]

BASE_RESERVE = {
    "bookshelf": 95,
    "chair": 80,
    "lamp": 45,
    "desk": 130,
    "storage cabinet": 120,
    "side table": 70,
    "shoe rack": 60,
    "plant stand": 40,
    "utility cart": 75,
    "bench": 90,
}


def make_description(
    *,
    category: str,
    material: str,
    color: str,
    style: str,
    height_cm: int,
    capacity: int | None,
) -> str:
    parts = [
        f"A {style} {color} {material} {category}.",
        f"It is approximately {height_cm} cm tall.",
    ]

    if category == "bookshelf":
        parts.append(
            f"It is designed to hold about {capacity} books."
        )
    elif category == "shoe rack":
        parts.append(
            f"It stores approximately {capacity} pairs of shoes."
        )
    elif category == "storage cabinet":
        parts.append(
            f"It provides approximately {capacity} liters of storage."
        )
    elif category == "utility cart":
        parts.append(
            f"It supports approximately {capacity} kg of supplies."
        )
    else:
        parts.append(
            "It is intended for normal indoor household use."
        )

    return " ".join(parts)


def main() -> None:
    products: list[dict] = []

    for i in range(4000):
        category = CATEGORIES[i % len(CATEGORIES)]
        material = MATERIALS[(i // 3) % len(MATERIALS)]
        color = COLORS[(i // 7) % len(COLORS)]
        style = STYLES[(i // 11) % len(STYLES)]
        height_cm = HEIGHTS[(i // 5) % len(HEIGHTS)]

        capacity: int | None = None

        if category == "bookshelf":
            capacity = BOOK_CAPACITIES[(i // 13) % len(BOOK_CAPACITIES)]

        elif category == "shoe rack":
            capacity = [6, 8, 10, 12, 16][
                (i // 13) % 5
            ]

        elif category == "storage cabinet":
            capacity = [40, 60, 80, 100, 120][
                (i // 13) % 5
            ]

        elif category == "utility cart":
            capacity = [20, 30, 40, 50, 60][
                (i // 13) % 5
            ]

        reserve = (
            BASE_RESERVE[category]
            + MATERIALS.index(material) * 8
            + STYLES.index(style) * 5
            + max(0, height_cm - 55) // 10
            + random.randint(0, 15)
        )

        if capacity is not None:
            reserve += int(capacity * 0.3)

        product = {
            "sku": f"ARUN-{i + 1:04d}",
            "name": (
                f"{style.title()} "
                f"{material.title()} "
                f"{category.title()} "
                f"{i + 1:04d}"
            ),
            "description": make_description(
                category=category,
                material=material,
                color=color,
                style=style,
                height_cm=height_cm,
                capacity=capacity,
            ),
            "reserve": int(reserve),
            "category": category,
            "material": material,
            "color": color,
            "style": style,
            "height_cm": height_cm,
            "capacity": capacity,
        }

        products.append(product)

    OUTPUT.write_text(
        json.dumps(
            products,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Wrote {len(products)} products to {OUTPUT}"
    )


if __name__ == "__main__":
    main()