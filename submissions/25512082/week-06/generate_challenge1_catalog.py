"""Generate the deterministic, synthetic Challenge 1 product catalog."""
from __future__ import annotations

import json
from pathlib import Path


OUTPUT = Path(__file__).resolve().parent / "catalog" / "challenge1_catalog.jsonl"
MATERIALS = ("oak wood", "pine wood", "bamboo", "steel", "aluminum", "glass")
COLORS = ("natural", "walnut", "white", "black", "green")
CATEGORIES = (
    ("bookshelf", "stores books and documents on adjustable shelves", 240),
    ("desk", "supports study, writing, and computer work", 170),
    ("chair", "provides ergonomic seating for home or office", 120),
    ("lamp", "provides focused adjustable light for reading", 70),
    ("cabinet", "stores household supplies behind closed doors", 210),
    ("table", "provides a shared surface for dining and meetings", 190),
    ("stool", "provides compact backless seating", 80),
    ("rack", "organizes shoes, coats, or equipment", 110),
)


def products():
    yield {
        "id": "bookshelf-low-oak-40",
        "name": "Low Oak Forty-Book Shelf",
        "description": (
            "A low wooden bookshelf sized to keep about 40 books. "
            "Its 72 cm height stays below a typical desk."
        ),
        "reserve": 260,
    }
    for category_index, (category, purpose, base) in enumerate(CATEGORIES):
        for number in range(1, 376):
            material = MATERIALS[(number + category_index) % len(MATERIALS)]
            color = COLORS[(number * 3 + category_index) % len(COLORS)]
            capacity = 12 + (number * 7 + category_index * 5) % 89
            height = 45 + (number * 11 + category_index * 3) % 151
            reserve = base + (number * 13 + category_index * 17) % 181
            yield {
                "id": f"{category}-{number:04d}",
                "name": f"{color.title()} {material.title()} {category.title()} {number:04d}",
                "description": (
                    f"A {color} {material} {category} that {purpose}. "
                    f"Capacity rating {capacity}; height {height} cm."
                ),
                "reserve": reserve,
            }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as stream:
        for product in products():
            stream.write(json.dumps(product, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
