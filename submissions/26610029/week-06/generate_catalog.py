"""Challenge 1 -- builds catalog.json: a few thousand furniture items (name,
description, reserve) from type x material x size x feature combinations, so
a buyer can describe what they want in free text ("something wooden to keep
about 40 books, not taller than my desk") instead of naming an exact product.

    python generate_catalog.py   # writes catalog.json next to this file
"""
import itertools
import json
import random

random.seed(7)

WOOD_MATERIALS = {"oak", "walnut", "pine", "maple", "birch", "reclaimed wood", "bamboo"}

TYPES = {
    "bookshelf":  {"base": 150, "use": "to store books",             "cap_unit": "books",           "cap_range": (20, 80)},
    "desk":       {"base": 200, "use": "as a work surface",          "cap_unit": None,              "cap_range": None},
    "chair":      {"base": 90,  "use": "for seating",                "cap_unit": None,              "cap_range": None},
    "lamp":       {"base": 60,  "use": "to light a room",            "cap_unit": None,              "cap_range": None},
    "table":      {"base": 180, "use": "for dining or work",         "cap_unit": "place settings",  "cap_range": (2, 8)},
    "nightstand": {"base": 70,  "use": "beside a bed",               "cap_unit": None,              "cap_range": None},
    "stool":      {"base": 50,  "use": "for extra seating",          "cap_unit": None,              "cap_range": None},
    "cabinet":    {"base": 220, "use": "for storage",                "cap_unit": "liters",          "cap_range": (50, 300)},
    "bench":      {"base": 140, "use": "for seating by an entryway", "cap_unit": None,              "cap_range": None},
    "wardrobe":   {"base": 300, "use": "to hang clothes",            "cap_unit": "garments",        "cap_range": (15, 60)},
}
MATERIALS = {
    "oak": 1.3, "walnut": 1.5, "pine": 1.0, "maple": 1.25, "bamboo": 1.1,
    "metal": 1.15, "glass": 1.2, "wicker": 0.9, "rattan": 0.95,
    "painted MDF": 0.8, "reclaimed wood": 1.1, "birch": 1.05,
}
SIZES = {
    "compact":     (0.85, "about 60cm tall"),
    "standard":    (1.00, "about 80cm tall"),
    "wide":        (1.15, "about 90cm wide"),
    "tall":        (1.20, "about 150cm tall"),
    "low-profile": (0.90, "about 40cm tall, lower than a desk"),
    "extra-large": (1.35, "about 180cm tall"),
}
FEATURES = [
    ("with drawers", 25), ("foldable", 10), ("with wheels", 15), ("with a shelf", 10),
    ("stackable", 5), ("with a cushioned seat", 20), ("with adjustable height", 30),
    ("with a mirror", 20), ("with hooks", 8), (None, 0),
]

rows = []
for t, mat, size, feat in itertools.product(TYPES, MATERIALS, SIZES, FEATURES):
    info = TYPES[t]
    mat_mult = MATERIALS[mat]
    size_mult, height_phrase = SIZES[size]
    feat_phrase, feat_bonus = feat

    material_tag = f"wooden ({mat})" if mat in WOOD_MATERIALS else mat
    name = f"{size} {mat} {t}" + (f" {feat_phrase}" if feat_phrase else "")

    desc = f"A {size} {t} in {material_tag}"
    if feat_phrase:
        desc += f", {feat_phrase}"
    desc += f", good {info['use']}. Stands {height_phrase}."
    if info["cap_unit"]:
        cap = random.randint(*info["cap_range"])
        desc += f" Holds about {cap} {info['cap_unit']}."

    reserve = max(20, round(info["base"] * mat_mult * size_mult + feat_bonus + random.randint(-5, 5)))
    rows.append({"name": name, "description": desc, "reserve": reserve})

with open("catalog.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)

print(len(rows), "items written to catalog.json")
