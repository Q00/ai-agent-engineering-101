"""Generate a deterministic furniture catalog for the large-context challenge."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def make_catalog() -> list[dict]:
    products: list[dict] = []
    shelf_materials = ("wood", "bamboo", "metal", "plastic")
    finishes = ("natural", "walnut", "white", "black", "oak", "gray")
    for material in shelf_materials:
        for capacity in range(20, 100, 10):
            for height in range(50, 130, 10):
                for finish in finishes:
                    ident = f"shelf-{material}-{capacity}-{height}-{finish}"
                    products.append({
                        "id": ident,
                        "name": f"{finish} {material} bookshelf for {capacity} books",
                        "description": (
                            f"A {material} bookshelf that holds about {capacity} books. "
                            f"Height {height} cm; {finish} finish. "
                            + ("Fits below a standard 75 cm desk." if height <= 70 else "Tall storage unit.")
                        ),
                        "category": "bookshelf", "material": material,
                        "capacity_books": capacity, "height_cm": height,
                        "reserve": 90 + capacity + height // 2 + shelf_materials.index(material) * 8,
                    })
    for material in ("wood", "metal", "plastic"):
        for style in ("dining", "office", "lounge", "folding", "stool"):
            for color in ("natural", "white", "black", "blue", "gray"):
                for variant in range(1, 7):
                    products.append({
                        "id": f"chair-{material}-{style}-{color}-{variant}",
                        "name": f"{color} {material} {style} chair {variant}",
                        "description": f"A {color} {material} {style} chair for seating; design variant {variant}.",
                        "category": "chair", "material": material,
                        "reserve": 150 + 5 * variant + 7 * (style == "office"),
                    })
    for style in ("desk", "floor", "reading"):
        for color in ("white", "black", "brass", "silver", "blue"):
            for size in ("compact", "medium", "large", "wide", "slim", "tall"):
                for variant in range(1, 5):
                    products.append({
                        "id": f"lamp-{style}-{color}-{size}-{variant}",
                        "name": f"{color} {size} {style} lamp {variant}",
                        "description": f"A {color} {size} {style} lamp for lighting; design variant {variant}.",
                        "category": "lamp", "material": "metal",
                        "reserve": 70 + 4 * variant + (15 if style == "floor" else 0),
                    })
    return products


if __name__ == "__main__":
    products = make_catalog()
    (ROOT / "catalog.json").write_text(json.dumps(products, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(products)} products")
