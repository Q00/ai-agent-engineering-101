"""
generate a reproducible synthetic inventory
"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("items.jsonl")
MATERIALS = ("wood", "metal", "plastic", "bamboo", "fabric")
COLORS = ("natural", "pink", "white", "blue", "gray")

def generate():
    for category in ("bookshelf", "chair", "lamp"):
        for material in MATERIALS:
            for color in COLORS:
                for variant in range(40):
                    height = 55 + (variant % 8) * 5
                    capacity = 20 + (variant % 9) * 10
                    ident = f"{category}-{material}-{color}-{variant:02d}"
                    if category == "bookshelf":
                        purpose = f"keep {capacity} books on a {height} cm tall shelf"
                        reserve = 260 + variant * 2 + MATERIALS.index(material) * 8
                    elif category == "chair":
                        purpose = f"sit at a desk, seat height {height} cm"
                        reserve = 180 + variant * 2 + MATERIALS.index(material) * 7
                    else:
                        purpose = f"reading light for a desk, {height} cm tall"
                        reserve = 100 + variant + MATERIALS.index(material) * 6
                    yield {
                        "id": ident, "name": f"{color} {material} {category} {variant:02d}",
                        "description": f"A {material} {color} {category} to {purpose}.",
                        "category": category, "material": material, "color": color,
                        "height_cm": height, "capacity_books": capacity if category == "bookshelf" else 0,
                        "reserve": reserve,
                    }


if __name__ == "__main__":
    with OUT.open("w", encoding="utf-8") as target:
        for item in generate():
            target.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"Wrote 3000 entries to {OUT}")







