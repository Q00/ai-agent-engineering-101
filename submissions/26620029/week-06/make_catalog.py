"""Generates catalog.json: ~3000 furniture items with attributes, a description and a reserve.

    python make_catalog.py            # writes catalog.json (seed 6, deterministic)

Every item: sku, name, category, material, color, height_cm, width_cm, depth_cm,
features (list), capacity (books / pairs / seats, or null), size (rugs), description,
reserve (lowest price the shop may accept) and list_price.

The three trade-test items (name "lamp", "chair", "bookshelf"; reserves 100, 180, 260)
are kept as fixed SKUs so the week-06 trade test still finds them by name.
"""
import json
import random
from pathlib import Path

SEED = 6
PER_CATEGORY = 176          # 17 categories -> 2992 generated + 3 fixed = 2995 items

STYLES = ["Nordic", "Loft", "Classic", "Mid-Century", "Urban", "Rustic", "Minimal", "Studio",
          "Coastal", "Heritage", "Zen", "Industrial", "Bauhaus", "Cottage", "Metro", "Alpine"]
COLORS = ["natural", "white", "black", "grey", "walnut brown", "oak beige", "navy", "green", "red", "cream"]
WOODS = {"oak": 1.25, "pine": 1.0, "walnut": 1.4, "birch": 1.1, "bamboo": 1.05}
WOOD_LIKE = set(WOODS)

FIXED = [
    {"sku": "BASE-LAMP", "name": "lamp", "category": "desk lamp", "material": "metal", "color": "black",
     "height_cm": 45, "width_cm": 18, "depth_cm": 18, "features": [], "capacity": None, "size": None,
     "description": "Plain metal desk lamp.", "reserve": 100, "list_price": 130},
    {"sku": "BASE-CHAIR", "name": "chair", "category": "dining chair", "material": "pine", "color": "natural",
     "height_cm": 85, "width_cm": 45, "depth_cm": 50, "features": [], "capacity": 1, "size": None,
     "description": "Plain pine chair.", "reserve": 180, "list_price": 234},
    {"sku": "BASE-BOOKSHELF", "name": "bookshelf", "category": "bookshelf", "material": "pine", "color": "natural",
     "height_cm": 180, "width_cm": 80, "depth_cm": 30, "features": [], "capacity": 160, "size": None,
     "description": "Plain pine bookshelf, five shelves.", "reserve": 260, "list_price": 338},
]


def pick(r, d):
    """Pick a key of a {key: factor} dict, return (key, factor)."""
    k = r.choice(list(d))
    return k, d[k]


def gen(r, cat):
    """One item of a category: attributes, then a description and a reserve from them."""
    feats, cap, size = [], None, None
    if cat == "bookshelf":
        mat, f = pick(r, {**WOODS, "metal": 0.95, "particleboard": 0.7})
        shelves = r.randint(2, 6)
        h = shelves * r.randint(30, 38) + r.randint(0, 10)
        w = r.choice([40, 60, 80, 100, 120, 140])
        cap = shelves * w // 3
        if r.random() < 0.3:
            feats.append("wall-mountable")
        base = (90 + cap * 1.6) * f
        desc = f"{mat} bookshelf with {shelves} shelves, {h} cm tall and {w} cm wide; holds about {cap} books"
    elif cat in ("desk lamp", "floor lamp"):
        mat, f = pick(r, {"metal": 1.0, "ceramic": 1.1, "glass": 1.15, "oak": 1.2, "bamboo": 1.0, "plastic": 0.7})
        h = r.randint(30, 60) if cat == "desk lamp" else r.randint(130, 190)
        w = r.randint(12, 30) if cat == "desk lamp" else r.randint(25, 50)
        for x, p in (("dimmable", 0.5), ("USB charging port", 0.25), ("warm white", 0.5), ("adjustable arm", 0.4)):
            if r.random() < p:
                feats.append(x)
        base = ((45 if cat == "desk lamp" else 110) + 20 * len(feats)) * f
        desc = f"{mat} {cat}, {h} cm tall" + (", " + ", ".join(feats) if feats else "")
    elif cat in ("office chair", "dining chair", "stool"):
        mat, f = pick(r, {"mesh": 1.0, "leather": 1.5, "fabric": 1.0, "oak": 1.2, "pine": 0.9, "plastic": 0.6, "metal": 0.9})
        h = {"office chair": r.randint(95, 125), "dining chair": r.randint(78, 95), "stool": r.randint(45, 80)}[cat]
        w = r.randint(35, 70)
        cap = 1
        opts = {"office chair": (("lumbar support", 0.5), ("adjustable height", 0.8), ("armrests", 0.6), ("headrest", 0.3)),
                "dining chair": (("stackable", 0.3), ("cushioned seat", 0.5)),
                "stool": (("adjustable height", 0.4), ("foldable", 0.3))}[cat]
        for x, p in opts:
            if r.random() < p:
                feats.append(x)
        base = ({"office chair": 140, "dining chair": 60, "stool": 30}[cat] + 30 * len(feats)) * f
        desc = f"{mat} {cat}, seat {w} cm wide, {h} cm tall" + (", " + ", ".join(feats) if feats else "")
    elif cat == "desk":
        mat, f = pick(r, {**WOODS, "metal": 0.9, "particleboard": 0.7, "glass": 1.1})
        w = r.choice([80, 100, 120, 140, 160, 180])
        adj = r.random() < 0.3
        h = r.randint(70, 78) if not adj else 0
        if adj:
            feats.append("height adjustable (standing desk)")
            h = r.randint(65, 125)
        if r.random() < 0.4:
            feats.append("drawers")
        base = (150 + w * 1.1 + (260 if adj else 0) + (40 if "drawers" in feats else 0)) * f
        desc = f"{mat} desk, {w} cm wide, " + ("adjustable from 65 to 125 cm" if adj else f"{h} cm tall") \
               + (", with drawers" if "drawers" in feats else "")
    elif cat in ("coffee table", "side table"):
        mat, f = pick(r, {**WOODS, "glass": 1.1, "marble": 1.6, "metal": 0.9})
        h = r.randint(35, 50) if cat == "coffee table" else r.randint(45, 65)
        w = r.randint(80, 130) if cat == "coffee table" else r.randint(35, 55)
        if r.random() < 0.35:
            feats.append("storage shelf")
        base = ((110 if cat == "coffee table" else 50) + w * 0.6) * f
        desc = f"{mat} {cat}, {w} cm across, {h} cm tall" + (", with storage shelf" if feats else "")
    elif cat == "rug":
        mat, f = pick(r, {"wool": 1.5, "cotton": 1.0, "jute": 0.9, "synthetic": 0.7, "silk": 2.2})
        wl = r.choice([(60, 90), (80, 150), (120, 170), (140, 200), (160, 230), (200, 300)])
        size, h, w = f"{wl[0]}x{wl[1]}", 1, wl[0]
        if r.random() < 0.4:
            feats.append("washable")
        base = (30 + wl[0] * wl[1] / 10000 * 60) * f
        desc = f"{mat} rug, {size} cm" + (", machine washable" if feats else "")
    elif cat == "shoe cabinet":
        mat, f = pick(r, {**WOODS, "particleboard": 0.7, "metal": 0.9})
        cap = r.choice([8, 12, 16, 20, 24, 30, 40])
        h, w = 40 + cap * 3, r.randint(60, 120)
        base = (50 + cap * 4) * f
        desc = f"{mat} shoe cabinet for {cap} pairs of shoes, {h} cm tall"
    elif cat == "sofa":
        mat, f = pick(r, {"fabric": 1.0, "leather": 1.6, "velvet": 1.25, "linen": 1.1})
        cap = r.choice([2, 2, 3, 3, 4])
        h, w = r.randint(75, 95), cap * 70 + r.randint(10, 40)
        if r.random() < 0.3:
            feats.append("sofa bed")
        base = (cap * 230 + (150 if feats else 0)) * f
        desc = f"{mat} sofa for {cap} people, {w} cm wide" + (", converts to a bed" if feats else "")
    elif cat == "mirror":
        mat, f = pick(r, {"oak": 1.2, "metal": 1.0, "rattan": 1.1, "frameless": 0.7})
        h, w = r.choice([(60, 40), (100, 50), (160, 50), (180, 80)])
        base = (30 + h * w / 100 * 0.5) * f
        desc = f"{mat} mirror, {h} x {w} cm"
    elif cat == "wardrobe":
        mat, f = pick(r, {**WOODS, "particleboard": 0.7})
        doors = r.choice([2, 3, 4])
        h, w = r.randint(170, 220), doors * 50
        base = (200 + doors * 90) * f
        desc = f"{mat} wardrobe with {doors} doors, {h} cm tall, {w} cm wide"
    elif cat == "bed frame":
        mat, f = pick(r, {**WOODS, "metal": 0.9, "fabric": 1.1})
        size = r.choice(["single", "double", "queen", "king"])
        h, w = r.randint(30, 110), {"single": 90, "double": 140, "queen": 160, "king": 180}[size]
        base = (180 + w * 1.5) * f
        desc = f"{mat} {size} bed frame, {w} cm wide"
    elif cat == "coat rack":
        mat, f = pick(r, {**WOODS, "metal": 0.9})
        cap = r.choice([4, 6, 8, 10])
        h, w = r.randint(160, 190), 40
        base = (20 + cap * 6) * f
        desc = f"{mat} standing coat rack with {cap} hooks, {h} cm tall"
    else:  # plant stand
        mat, f = pick(r, {**WOODS, "metal": 0.9, "ceramic": 1.1})
        cap = r.choice([1, 2, 3, 5])
        h, w = r.randint(30, 120), r.randint(20, 80)
        base = (15 + cap * 10) * f
        desc = f"{mat} plant stand for {cap} pots, {h} cm tall"
    reserve = max(15, int(round(base * r.uniform(0.9, 1.1))))
    color = r.choice(COLORS)
    return {"category": cat, "material": mat, "color": color, "height_cm": h, "width_cm": w,
            "depth_cm": r.randint(25, 70), "features": feats, "capacity": cap, "size": size,
            "description": f"{desc[0].upper()}{desc[1:]}. Color: {color}.",
            "reserve": reserve, "list_price": int(reserve * r.uniform(1.3, 1.6))}


CATEGORIES = ["bookshelf", "desk lamp", "floor lamp", "office chair", "dining chair", "stool", "desk",
              "coffee table", "side table", "rug", "shoe cabinet", "sofa", "mirror", "wardrobe",
              "bed frame", "coat rack", "plant stand"]


def main():
    r = random.Random(SEED)
    items = list(FIXED)
    for ci, cat in enumerate(CATEGORIES):
        for i in range(PER_CATEGORY):
            it = gen(r, cat)
            it["sku"] = f"{cat[:3].upper()}-{ci:02d}{i:03d}"
            it["name"] = f"{r.choice(STYLES)} {it['material'].title()} {cat.title()} {r.choice('ABCDEFGHJK')}{r.randint(10, 99)}"
            items.append(it)
    out = Path(__file__).with_name("catalog.json")
    out.write_text(json.dumps(items, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {len(items)} items to {out.name}")


if __name__ == "__main__":
    main()
