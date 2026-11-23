"""Generate the deterministic 1,200-product catalog committed for Challenge 1."""

from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path


ANCHORS = [
    {
        "id": "anchor-bookshelf-40",
        "name": "Low Oak Forty-Book Shelf",
        "description": "wooden storage for about 40 books, below desk height",
        "reserve": 260,
    },
    {
        "id": "anchor-reading-lamp",
        "name": "Adjustable Reading Lamp",
        "description": "warm adjustable desk light for reading books",
        "reserve": 100,
    },
    {
        "id": "anchor-office-chair",
        "name": "Mesh Office Chair",
        "description": "ergonomic chair with lumbar support for a small office",
        "reserve": 180,
    },
]


def generate_products(count: int = 1200) -> list[dict[str, object]]:
    """Return deterministic products with varied searchable descriptions."""
    if count < len(ANCHORS):
        raise ValueError(f"count must be at least {len(ANCHORS)}")
    values = list(ANCHORS)
    categories = (
        ("bookshelf", "storage shelves for books and documents", 220),
        ("lamp", "adjustable light for a desk or bedside", 80),
        ("chair", "supportive seating for work and reading", 150),
        ("cabinet", "closed storage for office supplies", 240),
        ("table", "flat work surface for a compact room", 200),
    )
    materials = ("oak wooden", "pine wooden", "steel", "bamboo", "recycled plastic")
    sizes = ("low", "desk-height", "tall", "narrow", "wide", "compact")
    colors = ("natural", "black", "white", "green", "blue", "walnut")
    index = 0
    for (category, purpose, base), material, size, color in product(
        categories, materials, sizes, colors
    ):
        if len(values) >= count:
            break
        capacity = 20 + (index % 9) * 10
        values.append(
            {
                "id": f"product-{index:04d}",
                "name": f"{color.title()} {material.title()} {size.title()} {category.title()}",
                "description": (
                    f"{size} {material} {category}; {purpose}; capacity about "
                    f"{capacity} items; {color} finish"
                ),
                "reserve": base + (index % 11) * 7,
            }
        )
        index += 1
    while len(values) < count:
        category, purpose, base = categories[index % len(categories)]
        values.append(
            {
                "id": f"product-{index:04d}",
                "name": f"Series {index:04d} {category.title()}",
                "description": f"specialized {category}; {purpose}; model series {index:04d}",
                "reserve": base + (index % 17) * 5,
            }
        )
        index += 1
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1200)
    parser.add_argument("--output", type=Path, default=Path("catalog.json"))
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(generate_products(args.count), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.count} products to {args.output}")


if __name__ == "__main__":
    main()

