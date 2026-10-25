"""Catalog generator and search (challenge 1).

  python catalog.py            # writes catalog.json (deterministic, seed 6)

Each item: id, name, category, material, color, height_cm, width_cm, capacity, description,
reserve. Buyers describe what they want; search() narrows the catalog to a few candidates
in code, and only those go into the harness prompt.
"""
import json
import math
import random
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = {  # name -> (family word, price factor)
    "oak": ("wooden", 1.3), "walnut": ("wooden", 1.5), "pine": ("wooden", 0.9), "birch": ("wooden", 1.1),
    "steel": ("metal", 1.0), "aluminum": ("metal", 1.1), "glass": ("glass", 1.2), "bamboo": ("bamboo", 0.95),
    "rattan": ("woven", 1.0), "plastic": ("plastic", 0.6),
}
COLORS = ["natural", "white", "black", "grey", "dark brown", "green", "navy", "beige"]
# category -> (base price, height range, width range, capacity unit or None, capacity per width)
CATEGORIES = {
    "bookshelf": (180, (60, 200), (40, 120), "books", None),
    "desk": (220, (70, 78), (80, 180), None, None),
    "office chair": (160, (85, 120), (55, 70), None, None),
    "dining chair": (70, (78, 95), (40, 50), None, None),
    "desk lamp": (45, (30, 60), (12, 25), "lumens", None),
    "floor lamp": (90, (140, 185), (25, 45), "lumens", None),
    "dining table": (300, (72, 76), (80, 220), "seats", None),
    "coffee table": (120, (35, 50), (60, 130), None, None),
    "wardrobe": (420, (170, 220), (80, 200), "hangers", None),
    "nightstand": (80, (45, 65), (35, 55), "drawers", None),
    "shoe rack": (50, (40, 120), (50, 110), "pairs", None),
    "tv stand": (150, (40, 60), (100, 200), "inch tv", None),
    "sofa": (650, (75, 95), (150, 260), "seats", None),
    "stool": (40, (45, 80), (30, 40), None, None),
    "coat rack": (55, (160, 190), (40, 60), "hooks", None),
}
VARIANTS_PER_PAIR = 20  # 15 categories x 10 materials x 20 = 3000 items


def _capacity(cat, h, w, rng):
    if cat == "bookshelf":
        shelves = max(1, h // 35)
        return shelves * max(5, w // 3)  # books
    if cat in ("desk lamp", "floor lamp"):
        return rng.choice([300, 450, 600, 800, 1000, 1500])
    if cat == "dining table":
        return max(2, (w // 55) * 2)
    if cat == "sofa":
        return max(2, w // 65)
    if cat == "wardrobe":
        return w // 4
    if cat == "nightstand":
        return rng.choice([1, 2, 3])
    if cat == "shoe rack":
        return max(4, (h // 20) * (w // 25))
    if cat == "tv stand":
        return int(w / 2.3)
    if cat == "coat rack":
        return rng.choice([4, 6, 8, 10])
    return None


def generate(seed=6):
    rng = random.Random(seed)
    items, n = [], 0
    for cat, (base, hr, wr, unit, _) in CATEGORIES.items():
        for mat, (family, factor) in MATERIALS.items():
            for _ in range(VARIANTS_PER_PAIR):
                n += 1
                h, w = rng.randint(*hr), rng.randint(*wr)
                color = rng.choice(COLORS)
                cap = _capacity(cat, h, w, rng)
                size = (h * w) / ((sum(hr) / 2) * (sum(wr) / 2))
                reserve = int(round(base * factor * (0.75 + 0.25 * size) * rng.uniform(0.9, 1.1) / 5.0) * 5)
                reserve = max(10, reserve)
                cap_text = f" Holds about {cap} {unit}." if unit == "books" else (f" {cap} {unit}." if unit else "")
                if cat in ("desk lamp", "floor lamp"):
                    cap_text = f" {cap} lumens."
                name = f"{color.title()} {mat} {cat}" + (f", {cap} {unit}" if unit else "") + f", {h} cm"
                desc = (f"A {color} {family} {cat} made of {mat}. {h} cm tall, {w} cm wide.{cap_text}"
                        + (" Low enough to sit beside a desk." if h <= 80 and cat == "bookshelf" else ""))
                items.append({"id": f"c{n:05d}", "name": name, "category": cat, "material": mat,
                              "family": family, "color": color, "height_cm": h, "width_cm": w,
                              "capacity": cap, "unit": unit, "description": desc, "reserve": reserve})
    return items


# ---------------------------------------------------------------- search

SYNONYMS = {  # buyer words -> catalog words, so a description finds the category
    "couch": "sofa", "settee": "sofa", "light": "lamp", "lighting": "lamp", "standing": "floor",
    "closet": "wardrobe", "bedside": "nightstand", "television": "tv", "shoes": "shoe", "books": "bookshelf",
    "eat": "dining", "dinner": "dining", "seat": "chair", "wood": "wooden", "iron": "metal",
    "hangers": "wardrobe", "drawers": "nightstand", "people": "seats", "pairs": "shoe",
}


STOPWORDS = set("a an the i to for of and or with my me want buy about some something that can where "
                "is it in on at by be as made holds enough sit beside cm wide tall".split())
CATEGORY_WORDS = {w for c in CATEGORIES for w in c.split()}


def _tokens(text):
    words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS]
    return words + [SYNONYMS[w] for w in words if w in SYNONYMS]


class Index:
    """Token overlap (IDF-weighted) plus closeness of numbers in the query to the item's numbers."""

    def __init__(self, items):
        self.items = items
        self.docs = [set(_tokens(f"{i['name']} {i['description']} {i['category']} {i['family']}")) for i in items]
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log(len(items) / c) + 0.1 for t, c in df.items()}

    def search(self, query, k=12):
        q = set(_tokens(query))
        nums = [int(x) for x in re.findall(r"\d+", query)]
        scored = []
        for item, doc in zip(self.items, self.docs):
            s = sum(self.idf.get(t, 0) * (2 if t in CATEGORY_WORDS else 1) for t in q & doc)
            if nums:
                vals = [v for v in (item["height_cm"], item["width_cm"], item["capacity"]) if v]
                s += sum(max(0.0, 2.0 - min(abs(v - n) for v in vals) / 10) for n in nums)
            scored.append((s, item))
        scored.sort(key=lambda x: -x[0])
        return [i for _, i in scored[:k]]


if __name__ == "__main__":
    items = generate()
    (HERE / "catalog.json").write_text(json.dumps(items, indent=0), encoding="utf-8")
    print(f"wrote {len(items)} items to catalog.json")
