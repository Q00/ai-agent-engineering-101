"""Create 20 original shop inventories with different products and reserves.

Private fixture inventories are server inputs, never buyer/planner inputs.
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASES = {"lamp": 100, "chair": 180, "bookshelf": 260, "desk": 300, "cabinet": 280}
SIZES = {"lamp": (30, 12, 0), "chair": (75, 44, 0), "bookshelf": (60, 40, 60),
         "desk": (75, 80, 0), "cabinet": (60, 40, 60)}
DESCRIPTIONS = {
    "lamp": "A white wooden light for reading, height at most 30 cm, width at most 12 cm.",
    "chair": "A white wooden seat for one person, height at most 75 cm, width at most 44 cm.",
    "bookshelf": "White wooden open storage for 60 books, height at most 60 cm, width at most 40 cm.",
    "desk": "A white wooden work surface for writing, height at most 75 cm, width at most 80 cm.",
    "cabinet": "White wooden enclosed storage with doors for 60 books, height at most 60 cm, width at most 40 cm.",
}


def generate():
    (ROOT / "shops").mkdir(parents=True, exist_ok=True)
    profiles = []
    for category, base in BASES.items():
        height, width, capacity = SIZES[category]
        for index in range(4):
            shop_id = f"{category}-{index + 1}"
            primary = {"item_id": category + "-white-wood", "name": "White wood " + category,
                       "description": DESCRIPTIONS[category], "category": category, "material": "wood",
                       "color": "white", "height_cm": height, "width_cm": width,
                       "capacity_books": capacity, "reserve": base + index * 20}
            alternate = {**primary, "item_id": f"{category}-alternative-{index + 1}",
                         "name": "Blue metal " + category, "color": "blue", "material": "metal",
                         "description": f"Blue metal {category}; height {height} cm, width {width + index * 2} cm.",
                         "width_cm": width + index * 2, "reserve": base + 10 + index * 25}
            path = ROOT / "shops" / (shop_id + ".jsonl")
            with path.open("w", encoding="utf-8", newline="\n") as output:
                for row in (primary, alternate):
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
            profile = {"name": f"{category.title()} Shop {index + 1}",
                       "price_guide": f"Indicative negotiable {category} prices: {base + index * 20 + 20} to {base + index * 20 + 100}."}
            profile_path = ROOT / "shops" / (shop_id + ".json")
            profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            profiles.append({"shop_id": shop_id, "catalog": "shops/" + path.name,
                             "profile": "shops/" + profile_path.name})
    (ROOT / "shops.json").write_text(json.dumps({"shops": profiles}, indent=2) + "\n", encoding="utf-8")
    items = [{"line_id": category, "item_id": category + "-white-wood", "category": category,
              "description": DESCRIPTIONS[category]} for category in BASES]
    (ROOT / "shopping-list.json").write_text(json.dumps({"total_budget": 1500, "items": items}, indent=2) + "\n", encoding="utf-8")
    print("Generated 20 two-product shops, private reserves, public profiles and a five-item shopping list.")


if __name__ == "__main__":
    generate()
