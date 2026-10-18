"""Week 06 challenge 1 — make_catalog.py: generate the furniture catalog (design/challenge-1-catalog.md).

    python make_catalog.py            # writes catalog.json (about 4,500 items)

Every item is built from combinations (subtype x material x size x color x style) with a
fixed seed, so the same catalog comes out on every machine. Capacity follows the size and
the reserve follows a formula, so both can be checked. Near-duplicates (same shape in
another wood, or another height) are kept on purpose: picking the right item has to depend
on what the buyer said.
"""
import itertools
import json
import random

SEED = 6
PER_CATEGORY = 450
COLORS = {"oak": ["natural", "light"], "pine": ["natural", "white"], "walnut": ["brown", "dark"],
          "particleboard": ["white", "black", "grey"], "metal": ["black", "white", "brass"],
          "mesh": ["black", "grey"], "fabric": ["grey", "beige", "blue", "green"],
          "leather": ["black", "brown", "cognac"], "velvet": ["green", "navy", "pink"],
          "glass": ["clear", "smoked"]}
STYLES = ["scandinavian", "industrial", "classic", "modern"]
MATERIAL_FACTOR = {"pine": 0.8, "particleboard": 0.7, "oak": 1.2, "walnut": 1.5, "metal": 1.0,
                   "mesh": 1.0, "fabric": 1.0, "leather": 1.6, "velvet": 1.3, "glass": 1.2}


def r5(x):
    return max(10, int(round(x / 5.0)) * 5)


# each category: subtypes, materials, size options -> dict of fields, base reserve, size factor,
# rooms, features per subtype, a name and a description template
CATEGORIES = {
    "bookshelf": dict(
        code="BS", base=110, rooms=["study", "living room", "kids room", "bedroom"],
        subtypes={"open": [], "closed-door": ["doors"], "ladder": ["leaning"], "cube": ["cube shelves"]},
        materials=["oak", "pine", "walnut", "particleboard", "metal"],
        sizes=[dict(width_cm=w, depth_cm=30, height_cm=h) for w in (40, 60, 80, 100, 120)
               for h in (72, 106, 150, 180, 200)]),
    "desk": dict(
        code="DK", base=150, rooms=["study", "office", "bedroom", "kids room"],
        subtypes={"writing": [], "computer": ["cable tray"], "standing": ["height adjustable"],
                  "corner": ["L-shaped"]},
        materials=["oak", "pine", "walnut", "particleboard", "metal"],
        sizes=[dict(width_cm=w, depth_cm=d, height_cm=75) for w in (80, 100, 120, 140, 160, 180)
               for d in (50, 60, 70, 80)]),
    "office chair": dict(
        code="OC", base=90, rooms=["office", "study"],
        subtypes={"task": ["swivel"], "mesh": ["breathable back", "swivel"], "gaming": ["headrest", "swivel"]},
        materials=["mesh", "fabric", "leather"],
        sizes=[dict(width_cm=w, depth_cm=60, height_cm=h, max_load_kg=k) for w in (55, 62, 70)
               for h in (95, 110, 125) for k in (100, 120, 150)]),
    "dining chair": dict(
        code="DC", base=45, rooms=["dining room", "kitchen"],
        subtypes={"wooden": [], "upholstered": ["cushioned seat"], "stackable": ["stackable"]},
        materials=["oak", "pine", "walnut", "metal", "fabric", "velvet"],
        sizes=[dict(width_cm=w, depth_cm=50, height_cm=h, seats=1) for w in (42, 46, 50, 55)
               for h in (78, 82, 88, 95)]),
    "sofa": dict(
        code="SF", base=320, rooms=["living room", "studio"],
        subtypes={"2-seat": [], "3-seat": [], "sofa bed": ["folds into a bed"], "corner": ["L-shaped"]},
        materials=["fabric", "leather", "velvet"],
        sizes=[dict(width_cm=w, depth_cm=d, height_cm=82) for w in (150, 180, 210, 240, 280)
               for d in (85, 95, 160)]),
    "bed": dict(
        code="BD", base=200, rooms=["bedroom", "kids room", "guest room"],
        subtypes={"single": [], "double": [], "queen": [], "bunk": ["ladder", "two beds stacked"]},
        materials=["oak", "pine", "walnut", "metal", "fabric"],
        sizes=[dict(width_cm=w, depth_cm=d, height_cm=h) for w in (90, 140, 160) for d in (200, 210)
               for h in (40, 90, 160)]),
    "wardrobe": dict(
        code="WR", base=220, rooms=["bedroom", "guest room", "hallway"],
        subtypes={"1-door": [], "2-door": [], "sliding": ["sliding doors"]},
        materials=["oak", "pine", "walnut", "particleboard"],
        sizes=[dict(width_cm=w, depth_cm=d, height_cm=h) for w in (50, 80, 100, 150, 200)
               for d in (55, 60) for h in (180, 200, 220)]),
    "chest of drawers": dict(
        code="CD", base=130, rooms=["bedroom", "kids room", "hallway"],
        subtypes={"3-drawer": [], "5-drawer": [], "wide": ["6 drawers"]},
        materials=["oak", "pine", "walnut", "particleboard"],
        sizes=[dict(width_cm=w, depth_cm=d, height_cm=h) for w in (40, 60, 80, 120) for d in (40, 48)
               for h in (70, 90, 110, 130)]),
    "table": dict(
        code="TB", base=160, rooms=["dining room", "kitchen", "living room"],
        subtypes={"dining": [], "coffee": ["low"], "side": ["small"], "folding": ["folds flat"]},
        materials=["oak", "pine", "walnut", "metal", "glass"],
        sizes=[dict(width_cm=w, depth_cm=d, height_cm=75) for w in (45, 90, 120, 160, 200, 240)
               for d in (45, 60, 80, 100)]),
    "lamp": dict(
        code="LP", base=30, rooms=["bedroom", "study", "living room", "office"],
        subtypes={"desk": ["adjustable arm"], "floor": ["tall"], "pendant": ["hangs from ceiling"],
                  "clip": ["clip-on"]},
        materials=["metal", "oak", "glass", "fabric"],
        sizes=[dict(width_cm=w, depth_cm=w, height_cm=h, lumens=l) for w in (12, 20, 30)
               for h in (35, 50, 150) for l in (300, 400, 800, 1200, 1600)]),
}


def capacity(cat, sub, size):
    """The capacity field for the category, derived from the size."""
    w, h = size["width_cm"], size["height_cm"]
    if cat == "bookshelf":
        shelves = max(2, h // 32)
        return {"shelves": shelves, "books": shelves * int(w / 2.5)}
    if cat == "desk":
        return {"monitors": max(1, w // 60)}
    if cat == "sofa":
        return {"seats": {"2-seat": 2, "3-seat": 3, "sofa bed": 3, "corner": 5}[sub]}
    if cat == "bed":
        return {"sleeps": 1 if w == 90 and sub != "bunk" else 2}
    if cat == "wardrobe":
        return {"hanging_m": round(w / 100.0, 1)}
    if cat == "chest of drawers":
        return {"drawers": {"3-drawer": 3, "5-drawer": 5, "wide": 6}[sub]}
    if cat == "table":
        return {"seats": 0 if sub in ("coffee", "side") else max(2, (w // 60) * 2)}
    return {}


def size_factor(size):
    volume = size["width_cm"] * size["depth_cm"] * size["height_cm"] / 1_000_000.0   # m3
    return 0.7 + min(volume, 2.0) * 0.6


def consistent(cat, sub, size):
    """Drop combinations no shop would sell (a 200 cm coffee table, a bunk bed 40 cm tall ...)."""
    w, h = size["width_cm"], size["height_cm"]
    if cat == "table" and sub in ("coffee", "side"):
        return w <= 120
    if cat == "table" and sub == "dining":
        return w >= 90
    if cat == "bed":
        if sub == "bunk":
            return w == 90 and h == 160
        return h != 160 and w == {"single": 90, "double": 140, "queen": 160}[sub]
    if cat == "lamp":
        return (sub == "floor") == (h == 150)
    if cat == "sofa":
        if (sub == "corner") != (size["depth_cm"] == 160):
            return False
        return {"2-seat": w <= 180, "3-seat": w >= 180, "sofa bed": 180 <= w <= 240, "corner": w >= 210}[sub]
    return True


def describe(it):
    cat, sub = it["category"], it["subtype"]
    dims = f"W{it['width_cm']} x D{it['depth_cm']} x H{it['height_cm']} cm"
    extra = {
        "bookshelf": f"with {it.get('shelves')} shelves, holds about {it.get('books')} books",
        "desk": f"with room for {it.get('monitors')} monitor(s)",
        "office chair": f"supports up to {it.get('max_load_kg')} kg",
        "dining chair": "sold as a single chair",
        "sofa": f"seats {it.get('seats')}",
        "bed": f"sleeps {it.get('sleeps')}",
        "wardrobe": f"with {it.get('hanging_m')} m of hanging rail",
        "chest of drawers": f"with {it.get('drawers')} drawers",
        "table": f"seats {it.get('seats')}" if it.get("seats") else "for drinks, books or a plant",
        "lamp": f"gives {it.get('lumens')} lumens",
    }[cat]
    feats = (", " + ", ".join(it["features"])) if it["features"] else ""
    return (f"{it['color'].capitalize()} {it['material']} {sub} {cat}, {extra}{feats}. {dims}. "
            f"{it['style'].capitalize()} style, good for a {' or '.join(it['rooms'])}.")


def build():
    rng = random.Random(SEED)
    catalog = []
    for cat, spec in CATEGORIES.items():
        combos = [(sub, mat, size, color, style)
                  for sub in spec["subtypes"] for mat in spec["materials"]
                  for size in spec["sizes"] if consistent(cat, sub, size)
                  for color in COLORS[mat] for style in STYLES]
        picked = rng.sample(combos, min(PER_CATEGORY, len(combos)))
        picked.sort(key=lambda c: (list(spec["subtypes"]).index(c[0]), c[1], c[2]["width_cm"], c[2]["height_cm"]))
        for n, (sub, mat, size, color, style) in enumerate(picked, 1):
            it = {"id": f"{spec['code']}-{n:04d}", "category": cat, "subtype": sub, "material": mat,
                  "color": color, "style": style, **size, **capacity(cat, sub, size),
                  "features": list(spec["subtypes"][sub]),
                  "rooms": rng.sample(spec["rooms"], 2)}
            it["name"] = f"{color.capitalize()} {mat.capitalize()} {sub.title()} {cat.title()} {size['width_cm']}"
            it["description"] = describe(it)
            reserve = r5(spec["base"] * MATERIAL_FACTOR[mat] * size_factor(size))
            it["reserve"] = reserve
            it["list_price"] = r5(reserve * rng.uniform(1.3, 1.6))
            catalog.append(it)
    return catalog


if __name__ == "__main__":
    catalog = build()
    with open("catalog.json", "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(it, ensure_ascii=False) for it in catalog) + "\n]\n")
    by_cat = {}
    for it in catalog:
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    print(f"catalog.json: {len(catalog)} items")
    for cat, n in by_cat.items():
        reserves = [it["reserve"] for it in catalog if it["category"] == cat]
        print(f"  {cat:17} {n:4}  reserve {min(reserves)}-{max(reserves)}")
    print("sample:", json.dumps(catalog[0], ensure_ascii=False)[:300])
