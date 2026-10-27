"""Generate 3,000 original synthetic products, deterministically, without an API."""
import itertools
import json
from pathlib import Path


def rows():
    dimensions = {
        "bookshelf": ([40, 60, 80, 100, 120], range(30, 121, 10), 260, "open storage for books"),
        "chair": ([65, 75, 85, 95, 105], range(40, 59, 2), 180, "a seat for one person"),
        "lamp": ([20, 30, 40, 50, 60], range(10, 29, 2), 100, "light for reading or working"),
        "desk": ([65, 70, 75, 80, 85], range(60, 151, 10), 300, "a work surface for writing"),
        "cabinet": ([40, 60, 80, 100, 120], range(30, 121, 10), 280, "enclosed storage with doors for books"),
    }
    for category, (heights, widths, base, purpose) in dimensions.items():
        variants = itertools.product(("wood", "metal", "plastic"),
                                     ("black", "white", "brown", "blue"), heights, widths)
        for index, (material, color, height, width) in enumerate(variants, 1):
            capacity = (height // 20) * (width // 20) * 10 if category in {"bookshelf", "cabinet"} else 0
            yield {"item_id": f"{category}-{index:04d}",
                   "name": f"{color.title()} {material} {category} {height}x{width}",
                   "description": f"{color} {material} {purpose}; height {height} cm, width {width} cm; holds {capacity} books.",
                   "category": category, "material": material, "color": color,
                   "height_cm": height, "width_cm": width, "capacity_books": capacity,
                   "reserve": base + height + width + {"wood": 30, "metal": 20, "plastic": 0}[material]}


if __name__ == "__main__":
    path = Path(__file__).with_name("products.jsonl")
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows():
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Generated 3000 original synthetic products: {path}")
