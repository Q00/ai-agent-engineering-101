"""Generate a reproducible, synthetic furniture catalog (4,000 distinct products).

These are invented teaching products, not scraped or real stock. Run this file
to reproduce data/catalog.json byte for byte; no model or network is involved.
"""
import itertools
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COLORS = ["natural", "white", "black", "blue", "green"]
SPEC = {
    "lamp": {"materials": ["wood", "metal", "plastic", "glass"],
             "heights": [25, 35, 45, 55, 65], "widths": [20, 25, 30, 35, 40],
             "styles": ["adjustable", "fixed"], "base": 50},
    "chair": {"materials": ["wood", "metal", "fabric", "plastic"],
              "heights": [75, 80, 85, 90, 95], "widths": [40, 45, 50, 55, 60],
              "styles": ["armless", "armrests"], "base": 75},
    "bookshelf": {"materials": ["wood", "metal", "plastic", "bamboo"],
                  "heights": [40, 55, 70, 85, 100], "widths": [30, 45, 60, 75, 90],
                  "styles": ["open", "backed"], "base": 90},
    "table": {"materials": ["wood", "metal", "plastic", "glass"],
              "heights": [65, 70, 75, 80, 85], "widths": [80, 100, 120, 140, 160],
              "styles": ["rectangular", "rounded"], "base": 110},
}


def generate():
    products = []
    for category, spec in SPEC.items():
        for ordinal, indices in enumerate(itertools.product(range(4), range(5), range(5), range(5), range(2)), 1):
            mi, hi, wi, ci, si = indices
            material, height, width = spec["materials"][mi], spec["heights"][hi], spec["widths"][wi]
            color, style = COLORS[ci], spec["styles"][si]
            capacity = (width // 3) * (1 if height < 70 else 2 if height < 100 else 3) if category == "bookshelf" else 0
            upholstered = category == "chair" and material == "fabric"
            name = f"{color.title()} {material} {category}, {style}, {height}h x {width}w cm"
            description = f"A {color} {material} {category} with {style} design, {height} cm tall and {width} cm wide."
            if category == "bookshelf":
                description += f" Stores up to {capacity} average 3-cm-thick books."
            elif category == "lamp":
                description += " Electric task lighting for a desk or reading corner."
            elif category == "chair":
                description += " Seating for a desk or dining table."
                description += " Fabric upholstery covers the seat and back." if upholstered else " A firm, unupholstered seat."
            else:
                description += " A freestanding work or dining surface."
            products.append({
                "id": f"{category}-{ordinal:04d}", "name": name, "description": description,
                "category": category, "material": material, "color": color, "style": style,
                "height_cm": height, "width_cm": width, "capacity_books": capacity,
                "upholstered": upholstered,
                # Manufacturing variants affect the inventory's reserve, never an offer policy.
                "reserve": spec["base"] + mi * 7 + hi * 4 + wi * 3 + ci * 2 + si * 5,
            })
    return products


def main():
    path = ROOT / "data" / "catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(generate(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(generate())} products at {path}")


if __name__ == "__main__":
    main()
