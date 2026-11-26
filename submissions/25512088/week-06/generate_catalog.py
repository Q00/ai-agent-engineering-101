"""Generate the deterministic 2,003-product catalog committed with this submission."""

from __future__ import annotations

import json
from pathlib import Path


MATERIALS = ["oak", "pine", "walnut", "steel", "bamboo", "glass", "linen", "rattan"]
STYLES = ["compact", "classic", "modern", "minimal", "studio", "family", "urban", "soft"]
KINDS = ["side table", "storage bench", "floor lamp", "desk chair", "wall shelf", "cabinet", "stool", "rack"]


def build() -> list[dict]:
    products = [
        {"id": "featured-bookshelf", "name": "Compact Oak Bookshelf", "description": "A wooden low bookshelf that keeps about 40 books and is not taller than a desk.", "reserve": 260},
        {"id": "featured-lamp", "name": "Adjustable Desk Lamp", "description": "A focused LED light for reading and desk work.", "reserve": 100},
        {"id": "featured-chair", "name": "Ergonomic Desk Chair", "description": "An adjustable chair with lumbar support for long sitting sessions.", "reserve": 180},
    ]
    for index in range(2000):
        material = MATERIALS[index % len(MATERIALS)]
        style = STYLES[(index // len(MATERIALS)) % len(STYLES)]
        kind = KINDS[(index // (len(MATERIALS) * len(STYLES))) % len(KINDS)]
        products.append({
            "id": f"catalog-{index:04d}",
            "name": f"{style.title()} {material.title()} {kind.title()} {index:04d}",
            "description": f"A {style} {material} {kind} for room setup variant {index:04d}; capacity class {(index % 12) + 1}.",
            "reserve": 70 + (index * 17) % 430,
        })
    return products


if __name__ == "__main__":
    target = Path(__file__).with_name("catalog.json")
    target.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(build())} products to {target}")
