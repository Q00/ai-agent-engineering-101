"""Generate a furniture catalog for challenge 1.

    python make_catalog.py                      # catalog.json, 3000 items, seed 6
    python make_catalog.py --n 3000 --seed 7 --out catalogs/shop7.json

Each item: id, name (no digits), category, material, height_cm, width_cm,
capacity (books, only for shelves), description, reserve.
"""
import argparse
import json
import random

CATEGORIES = {
    # category: (base reserve, height range cm, width range cm)
    "bookshelf": (180, (60, 220), (40, 160)),
    "bookcase": (220, (80, 220), (60, 180)),
    "wall shelf": (60, (20, 40), (40, 120)),
    "desk lamp": (60, (30, 70), (15, 30)),
    "floor lamp": (110, (140, 190), (25, 50)),
    "chair": (120, (75, 105), (40, 60)),
    "armchair": (260, (80, 110), (70, 100)),
    "stool": (50, (45, 80), (30, 40)),
    "desk": (240, (70, 78), (90, 180)),
    "side table": (90, (45, 70), (35, 60)),
    "coffee table": (150, (35, 50), (70, 130)),
    "cabinet": (230, (60, 180), (60, 120)),
    "sofa": (520, (75, 95), (150, 240)),
    "wardrobe": (420, (170, 220), (80, 200)),
    "mirror": (80, (50, 180), (30, 90)),
}
MATERIALS = {
    # material: (price factor, words that appear in the description)
    "oak": (1.4, "solid oak, wooden"),
    "walnut": (1.6, "walnut, wooden"),
    "pine": (0.9, "pine, wooden"),
    "birch plywood": (1.0, "birch plywood, wooden"),
    "bamboo": (1.0, "bamboo, wooden look"),
    "steel": (1.1, "powder-coated steel, metal"),
    "aluminum": (1.2, "brushed aluminum, metal, lightweight"),
    "glass": (1.3, "tempered glass"),
    "plastic": (0.6, "molded plastic, lightweight"),
    "rattan": (1.1, "woven rattan, natural fiber"),
}
STYLES = ["Nordic", "Classic", "Industrial", "Minimal", "Rustic", "Modern", "Vintage", "Studio"]
COLORS = ["natural", "white", "black", "grey", "dark brown", "light brown", "green", "navy"]
NAMES = ["Linden", "Aster", "Birk", "Cove", "Dune", "Elm", "Fjord", "Grove", "Harbor", "Iris",
         "Juniper", "Kestrel", "Lark", "Maple", "Nook", "Orbit", "Pebble", "Quill", "Ridge", "Sable",
         "Tide", "Umber", "Vale", "Willow", "Yarrow", "Zephyr"]
SUFFIX = ["", " Plus", " Mini", " Max", " Duo", " Lite", " Pro", " Air"]
SHELVES_PER_CM = 1 / 35  # one shelf per ~35 cm of height
BOOKS_PER_SHELF_PER_CM = 1 / 2.5  # ~2.5 cm per book


def make_item(i, rng):
    cat = rng.choice(list(CATEGORIES))
    base, (h0, h1), (w0, w1) = CATEGORIES[cat]
    mat = rng.choice(list(MATERIALS))
    factor, mat_words = MATERIALS[mat]
    h, w = rng.randint(h0, h1), rng.randint(w0, w1)
    style, color = rng.choice(STYLES), rng.choice(COLORS)
    name = f"{style} {rng.choice(NAMES)}{rng.choice(SUFFIX)} {cat.title()}"
    size = (h * w) / (((h0 + h1) / 2) * ((w0 + w1) / 2))  # relative to an average one
    reserve = max(20, int(round(base * factor * (0.7 + 0.3 * size) * rng.uniform(0.9, 1.1) / 5) * 5))

    item = {"id": f"F{i:05d}", "name": name, "category": cat, "material": mat,
            "height_cm": h, "width_cm": w, "color": color, "reserve": reserve}
    parts = [f"{style} {cat} in {mat_words}, {color} finish.", f"{h} cm tall, {w} cm wide."]
    if cat in ("bookshelf", "bookcase", "wall shelf"):
        shelves = max(1, int(h * SHELVES_PER_CM))
        cap = int(shelves * w * BOOKS_PER_SHELF_PER_CM)
        item["capacity_books"] = cap
        parts.append(f"{shelves} {'shelf' if shelves == 1 else 'shelves'}, holds about {cap} books.")
    if cat in ("desk lamp", "floor lamp"):
        parts.append(rng.choice(["Warm white LED.", "Dimmable LED.", "Adjustable arm and head."]))
    if cat in ("chair", "armchair", "sofa", "stool"):
        parts.append(rng.choice(["Cushioned seat.", "Hard seat, easy to clean.", "Stackable.", "Upholstered."]))
    item["description"] = " ".join(parts)
    return item


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--out", default="catalog.json")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    items = [make_item(i, rng) for i in range(args.n)]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(items)} items to {args.out}")