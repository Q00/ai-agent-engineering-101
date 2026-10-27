"""Builds the catalogs and the shop configs. Deterministic (seed 606), so the committed files can be rebuilt.

    python make_catalog.py

catalog/basic.json      the trade test's three items: lamp 100, chair 180, bookshelf 260
catalog/large.json      3000 items in 15 categories, each with a name, a description, attributes and a reserve
shops/warehouse.json    challenge 1: one shop that sells the whole large catalog
shops/shop-01..20.json  challenge 2: twenty shops, each a slice of the large catalog at its own price level
"""
import json
import random
from pathlib import Path

R = random.Random(606)
HERE = Path(__file__).parent
PER_CATEGORY = 200

GROUP = {
    "oak": "wood", "walnut": "wood", "pine": "wood", "birch": "wood", "beech": "wood", "teak": "wood", "ash": "wood",
    "mango wood": "wood", "steel": "metal", "iron": "metal", "aluminium": "metal", "brass": "metal", "glass": "glass",
    "marble": "stone", "ceramic": "ceramic", "bamboo": "bamboo", "rattan": "rattan", "plastic": "plastic",
    "laminate": "laminate", "leather": "leather", "fabric": "fabric", "velvet": "fabric", "linen": "fabric",
    "mesh": "fabric", "wool": "wool", "jute": "jute", "cotton": "cotton", "polypropylene": "plastic", "viscose": "fabric",
}
MFACTOR = {
    "oak": 1.25, "walnut": 1.4, "pine": 0.85, "birch": 1.05, "beech": 1.15, "teak": 1.45, "ash": 1.15, "mango wood": 1.1,
    "steel": 1.0, "iron": 0.95, "aluminium": 1.05, "brass": 1.35, "glass": 1.1, "marble": 1.5, "ceramic": 1.0,
    "bamboo": 0.9, "rattan": 1.0, "plastic": 0.6, "laminate": 0.7, "leather": 1.5, "fabric": 1.0, "velvet": 1.25,
    "linen": 1.1, "mesh": 0.95, "wool": 1.3, "jute": 0.9, "cotton": 0.8, "polypropylene": 0.6, "viscose": 0.95,
}
WOODS = ["oak", "walnut", "pine", "birch", "beech", "teak", "ash", "mango wood"]
STYLES = ["Nordic", "Industrial", "Classic", "Modern", "Rustic", "Mid-century", "Minimal", "Coastal", "Urban",
          "Vintage", "Bauhaus", "Japandi"]
COLORS = ["natural", "black", "white", "grey", "dark brown", "honey", "green", "navy", "beige", "terracotta"]
MODELS = ["Fjell", "Aster", "Bruno", "Cora", "Dune", "Elm", "Fable", "Greta", "Harbor", "Iris", "Juno", "Kai", "Lumen",
          "Mira", "Nord", "Otto", "Pia", "Quill", "Rhea", "Sol", "Tove", "Uma", "Vale", "Wren", "Yara", "Zeno", "Alba",
          "Birk", "Cleo", "Dag", "Edda", "Finn", "Gale", "Hugo", "Ines", "Jarl", "Kira", "Lund", "Mako", "Nils", "Orla",
          "Pax", "Rune", "Saga", "Tilda", "Ulf", "Vida", "Wilma", "Ylva", "Zara"]


def mword(m):
    return f"solid {m}" if m == "mango wood" else (f"solid {m} wood" if m in WOODS else m)


def n(k, word):
    return f"{k} {word}" + ("" if k == 1 else "s")


def lamp():
    t = R.choice(["desk lamp", "floor lamp", "bedside lamp", "clip-on lamp", "pendant lamp", "wall lamp"])
    m = R.choice(["steel", "brass", "aluminium", "glass", "rattan", "bamboo", "oak", "ceramic", "plastic"])
    lo, hi = {"desk lamp": (35, 60), "floor lamp": (140, 185), "bedside lamp": (25, 45), "clip-on lamp": (18, 35),
              "pendant lamp": (20, 40), "wall lamp": (25, 40)}[t]
    a = {"height_cm": R.randint(lo, hi), "lumens": R.choice(range(250, 2001, 50))}
    f = {"dimmable": R.random() < 0.5}
    base = {"desk lamp": 70, "floor lamp": 130, "bedside lamp": 55, "clip-on lamp": 40, "pendant lamp": 90,
            "wall lamp": 75}[t]
    use = {"desk lamp": "for a desk or a work table", "floor lamp": "that stands on the floor and lights a corner",
           "bedside lamp": "for a nightstand next to the bed", "clip-on lamp": "that clips onto a shelf, a desk edge or a bed frame",
           "pendant lamp": "that hangs from the ceiling", "wall lamp": "mounted on the wall"}[t]
    d = (f"A {m} {t} {use}. {a['height_cm']} cm tall, {a['lumens']} lumens LED, "
         + ("dimmable." if f["dimmable"] else "not dimmable."))
    return t, m, a, f, base, 0.8 + a["lumens"] / 2000 * 0.5, d


def chair():
    t = R.choice(["office chair", "dining chair", "armchair", "folding chair", "gaming chair", "kids chair"])
    m = R.choice({"office chair": ["mesh", "fabric", "leather"], "dining chair": ["oak", "beech", "walnut", "steel", "plastic", "rattan", "velvet"],
                  "armchair": ["fabric", "leather", "velvet", "rattan", "linen"], "folding chair": ["steel", "aluminium", "beech", "plastic"],
                  "gaming chair": ["leather", "fabric", "mesh"], "kids chair": ["plastic", "pine", "birch"]}[t])
    seat = R.randint(30, 36) if t == "kids chair" else R.randint(42, 52)
    a = {"seat_height_cm": seat, "height_cm": seat + R.randint(35, 75),
         "max_load_kg": 60 if t == "kids chair" else R.choice([90, 100, 110, 120, 130, 150])}
    wheels = t in ("office chair", "gaming chair") and R.random() < 0.85
    arms = (t in ("office chair", "gaming chair") and R.random() < 0.8) or t == "armchair" or (t == "dining chair" and R.random() < 0.2)
    f = {"wheels": wheels, "armrests": arms}
    base = {"office chair": 160, "dining chair": 90, "armchair": 260, "folding chair": 40, "gaming chair": 220, "kids chair": 45}[t]
    d = (f"A {mword(m)} {t}" + (" on wheels" if wheels else "") + (" with armrests" if arms else " without armrests")
         + f". Seat height {seat} cm, {a['height_cm']} cm tall overall, holds up to {a['max_load_kg']} kg.")
    return t, m, a, f, base, 0.85 + (a["max_load_kg"] - 60) / 90 * 0.4, d


def bookshelf():
    t = R.choice(["bookcase", "wall shelf", "ladder shelf", "cube shelf"])
    m = R.choice(WOODS + ["steel", "bamboo", "plastic", "iron"])
    shelves = R.randint(1, 3) if t == "wall shelf" else R.choice([2, 2, 3, 3, 4, 5, 6])
    width = R.randint(40, 120)
    per = max(8, int(width / 2.6))
    if t == "wall shelf":
        height = shelves * R.randint(22, 30)
    elif t == "ladder shelf":
        height = shelves * 34 + 20
    else:
        height = shelves * R.randint(26, 34) + R.randint(4, 14)
    a = {"shelves": shelves, "capacity_books": shelves * per, "height_cm": height, "width_cm": width, "depth_cm": R.randint(22, 40)}
    f = {"wall_mounted": t == "wall shelf"}
    base = {"bookcase": 120, "wall shelf": 50, "ladder shelf": 110, "cube shelf": 90}[t]
    d = (f"A {mword(m)} {t} with {n(shelves, 'shelf').replace('shelfs', 'shelves')} that holds about {a['capacity_books']} books. "
         f"{height} cm tall, {width} cm wide, {a['depth_cm']} cm deep" + (", mounts on the wall." if f["wall_mounted"] else "."))
    return t, m, a, f, base, 0.6 + shelves * width / 600, d


def desk():
    t = R.choice(["writing desk", "computer desk", "standing desk", "corner desk", "kids desk"])
    m = R.choice(WOODS[:6] + ["steel", "glass", "bamboo"])
    width = R.randint(70, 100) if t == "kids desk" else R.randint(80, 180)
    adj = t == "standing desk" or (t == "kids desk" and R.random() < 0.5)
    h = R.randint(62, 72) if adj else R.randint(72, 76)
    a = {"width_cm": width, "depth_cm": R.randint(50, 80), "height_cm": h, "max_height_cm": h + (R.randint(40, 50) if adj else 0),
         "drawers": R.randint(0, 4)}
    f = {"adjustable_height": adj}
    base = {"writing desk": 180, "computer desk": 150, "standing desk": 380, "corner desk": 220, "kids desk": 110}[t]
    d = (f"A {mword(m)} {t}, {width} cm wide and {a['depth_cm']} cm deep, "
         + (f"height adjusts from {h} to {a['max_height_cm']} cm, " if adj else f"{h} cm high, ")
         + (f"{n(a['drawers'], 'drawer')}." if a["drawers"] else "no drawers."))
    return t, m, a, f, base, 0.7 + width / 180 * 0.5, d


def table():
    t = R.choice(["dining table", "coffee table", "side table", "folding table", "console table"])
    m = R.choice(WOODS[:6] + ["glass", "marble", "steel", "iron"])
    hlo, hhi = {"dining table": (74, 77), "coffee table": (35, 50), "side table": (40, 65), "folding table": (70, 75), "console table": (75, 85)}[t]
    llo, lhi = {"dining table": (120, 220), "coffee table": (80, 130), "side table": (35, 60), "folding table": (80, 180), "console table": (90, 150)}[t]
    length = R.randint(llo, lhi)
    seats = length // 50 * 2 if t == "dining table" else (length // 60 * 2 if t == "folding table" else 0)
    a = {"height_cm": R.randint(hlo, hhi), "length_cm": length, "seats": seats}
    base = {"dining table": 350, "coffee table": 160, "side table": 80, "folding table": 70, "console table": 180}[t]
    d = f"A {mword(m)} {t}, {length} cm long, {a['height_cm']} cm high" + (f", seats {seats}." if seats else ".")
    return t, m, a, {}, base, 0.7 + length / 220 * 0.5, d


def sofa():
    t = R.choice(["sofa", "loveseat", "sofa bed", "sectional sofa"])
    m = R.choice(["fabric", "leather", "velvet", "linen"])
    seats = {"sofa": R.randint(2, 4), "loveseat": 2, "sofa bed": R.randint(2, 3), "sectional sofa": R.randint(4, 6)}[t]
    a = {"seats": seats, "width_cm": seats * R.randint(60, 75) + R.randint(20, 40)}
    f = {"sleeper": t == "sofa bed"}
    base = {"sofa": 500, "loveseat": 380, "sofa bed": 650, "sectional sofa": 1100}[t]
    d = f"A {m} {t} for {seats} people, {a['width_cm']} cm wide" + (", folds out into a bed." if f["sleeper"] else ".")
    return t, m, a, f, base, 0.7 + seats * 0.15, d


def bed():
    t = R.choice(["single bed", "double bed", "queen bed", "king bed", "bunk bed"])
    m = R.choice(WOODS[:6] + ["steel", "fabric"])
    a = {"width_cm": {"single bed": 90, "double bed": 140, "queen bed": 160, "king bed": 180, "bunk bed": 90}[t],
         "height_cm": R.randint(150, 170) if t == "bunk bed" else R.randint(30, 110)}
    f = {"storage": R.random() < 0.4}
    base = {"single bed": 220, "double bed": 380, "queen bed": 480, "king bed": 620, "bunk bed": 450}[t]
    d = f"A {mword(m)} {t} for a {a['width_cm']} cm mattress, {a['height_cm']} cm high" + (", with storage drawers." if f["storage"] else ".")
    return t, m, a, f, base, 1.0, d


def wardrobe():
    m = R.choice(WOODS[:6] + ["laminate", "steel"])
    doors = R.randint(1, 4)
    a = {"doors": doors, "width_cm": doors * R.randint(45, 55), "height_cm": R.randint(180, 220)}
    f = {"mirror": R.random() < 0.3}
    d = f"A {mword(m)} wardrobe with {n(doors, 'door')}, {a['width_cm']} cm wide, {a['height_cm']} cm tall" + (", mirrored door." if f["mirror"] else ".")
    return "wardrobe", m, a, f, 300, 0.6 + doors * 0.25, d


def cabinet():
    t = R.choice(["tv stand", "filing cabinet", "sideboard", "display cabinet"])
    m = R.choice(WOODS[:6] + ["steel", "glass", "laminate"])
    a = {"width_cm": R.randint(40, 180), "height_cm": R.randint(45, 180), "doors": R.randint(0, 4), "drawers": R.randint(0, 4)}
    base = {"tv stand": 180, "filing cabinet": 140, "sideboard": 320, "display cabinet": 280}[t]
    d = (f"A {mword(m)} {t}, {a['width_cm']} cm wide, {a['height_cm']} cm tall, "
         f"{n(a['doors'], 'door') if a['doors'] else 'no doors'} and {n(a['drawers'], 'drawer') if a['drawers'] else 'no drawers'}.")
    return t, m, a, {}, base, 0.6 + a["width_cm"] / 180 * 0.6, d


def rug():
    m = R.choice(["wool", "jute", "cotton", "polypropylene", "viscose"])
    shape = R.choice(["rectangle"] * 7 + ["round", "round", "runner"])
    if shape == "round":
        length = width = R.choice([100, 120, 150, 160, 200, 240])
    elif shape == "runner":
        length, width = R.choice([200, 250, 300]), R.choice([60, 70, 80, 90])
    else:
        length, width = R.choice([(90, 60), (150, 80), (170, 120), (200, 140), (200, 150), (230, 160), (290, 200), (300, 250)])
    a = {"length_cm": length, "width_cm": width}
    pile = R.choice(["low", "medium", "high"])
    f = {"washable": R.random() < (0.7 if m in ("cotton", "polypropylene") else 0.15)}
    d = (f"A {pile}-pile {m} rug, {shape}, {length} x {width} cm, "
         + ("machine washable." if f["washable"] else "dry clean only."))
    return f"{shape} rug", m, a, f, 150, (length * width / 30000) ** 0.8, d


def mirror():
    t = R.choice(["wall mirror", "floor mirror", "vanity mirror"])
    m = R.choice(WOODS[:4] + ["brass", "steel", "rattan", "glass"])
    a = {"height_cm": {"wall mirror": R.randint(40, 120), "floor mirror": R.randint(150, 190), "vanity mirror": R.randint(25, 45)}[t],
         "width_cm": R.randint(25, 90)}
    base = {"wall mirror": 70, "floor mirror": 140, "vanity mirror": 45}[t]
    frame = "frameless" if m == "glass" else f"{mword(m)} frame"
    d = f"A {t} with a {frame}, {R.choice(['round', 'rectangular', 'arched'])}, {a['height_cm']} x {a['width_cm']} cm."
    return t, m, a, {}, base, 0.7 + a["height_cm"] * a["width_cm"] / 15000, d


def stool():
    t = R.choice(["bar stool", "counter stool", "step stool", "footstool"])
    m = R.choice(WOODS[:4] + ["steel", "plastic", "velvet", "leather"])
    lo, hi = {"bar stool": (72, 80), "counter stool": (60, 68), "step stool": (20, 45), "footstool": (30, 45)}[t]
    a = {"seat_height_cm": R.randint(lo, hi)}
    base = {"bar stool": 70, "counter stool": 65, "step stool": 30, "footstool": 45}[t]
    return t, m, a, {}, base, 1.0, f"A {mword(m)} {t}, seat height {a['seat_height_cm']} cm."


def shoe_rack():
    m = R.choice(["bamboo", "steel", "pine", "plastic", "oak"])
    tiers = R.randint(2, 6)
    a = {"tiers": tiers, "pairs": tiers * R.randint(3, 6), "width_cm": R.randint(50, 110)}
    d = f"A {mword(m)} shoe rack with {tiers} tiers for about {a['pairs']} pairs of shoes, {a['width_cm']} cm wide."
    return "shoe rack", m, a, {}, 35, 0.6 + a["pairs"] / 30, d


def coat_rack():
    t = R.choice(["standing coat rack", "wall coat rack"])
    m = R.choice(WOODS[:4] + ["steel", "iron", "brass"])
    a = {"hooks": R.randint(4, 12)}
    return t, m, a, {}, 45, 0.7 + a["hooks"] / 20, f"A {mword(m)} {t} with {a['hooks']} hooks."


def nightstand():
    m = R.choice(WOODS[:6] + ["steel", "rattan"])
    a = {"drawers": R.randint(0, 3), "height_cm": R.randint(40, 65), "width_cm": R.randint(35, 60)}
    d = (f"A {mword(m)} nightstand, {a['height_cm']} cm high, {a['width_cm']} cm wide, "
         + (f"{n(a['drawers'], 'drawer')}." if a["drawers"] else "an open shelf and no drawers."))
    return "nightstand", m, a, {}, 90, 0.8 + a["drawers"] * 0.12, d


CATEGORIES = {
    "lamp": ("LP", lamp), "chair": ("CH", chair), "bookshelf": ("BK", bookshelf), "desk": ("DK", desk),
    "table": ("TB", table), "sofa": ("SF", sofa), "bed": ("BD", bed), "wardrobe": ("WD", wardrobe),
    "cabinet": ("CB", cabinet), "rug": ("RG", rug), "mirror": ("MR", mirror), "stool": ("ST", stool),
    "shoe rack": ("SR", shoe_rack), "coat rack": ("CR", coat_rack), "nightstand": ("NS", nightstand),
}


def large():
    items = []
    for cat, (prefix, gen) in CATEGORIES.items():
        for i in range(1, PER_CATEGORY + 1):
            t, m, a, f, base, size, d = gen()
            style, color = R.choice(STYLES), R.choice(COLORS)
            name = f"{style} {m.title()} {t.title()} {R.choice(MODELS)}"
            reserve = max(15, round(base * MFACTOR[m] * size * R.uniform(0.85, 1.15)))
            items.append({"id": f"{prefix}-{i:04d}", "category": cat, "type": t, "name": name, "material": m,
                          "group": GROUP[m], "color": color, "style": style, "attrs": a, "flags": f,
                          "description": f"{d} {style} style, {color} finish.", "reserve": reserve})
    return items


BASIC = [
    {"id": "lamp", "category": "lamp", "type": "lamp", "name": "lamp", "material": "", "group": "", "attrs": {}, "flags": {},
     "description": "A desk lamp.", "reserve": 100},
    {"id": "chair", "category": "chair", "type": "chair", "name": "chair", "material": "", "group": "", "attrs": {}, "flags": {},
     "description": "A chair.", "reserve": 180},
    {"id": "bookshelf", "category": "bookshelf", "type": "bookshelf", "name": "bookshelf", "material": "", "group": "",
     "attrs": {}, "flags": {}, "description": "A bookshelf.", "reserve": 260},
]

SHOPS = [  # name, categories, share of those categories' items the shop carries, price factor
    ("Lumen & Co", ["lamp"], 0.5, 1.05),
    ("Bright Corner", ["lamp", "mirror"], 0.5, 0.95),
    ("Night Owl Lighting", ["lamp", "nightstand"], 0.5, 1.15),
    ("Sit Well", ["chair", "stool"], 0.5, 1.0),
    ("Office Works Supply", ["chair", "desk", "cabinet"], 0.5, 0.9),
    ("Ergo Studio", ["chair", "desk"], 0.5, 1.2),
    ("Shelf Life", ["bookshelf"], 0.6, 1.0),
    ("Oak & Iron", ["bookshelf", "table", "desk"], 0.5, 1.1),
    ("Book Nook Furniture", ["bookshelf", "nightstand"], 0.5, 0.9),
    ("Floor Story", ["rug"], 0.6, 1.0),
    ("Woven Home", ["rug", "mirror"], 0.5, 1.15),
    ("Rug Republic", ["rug", "coat rack"], 0.5, 0.9),
    ("Sofa Kingdom", ["sofa"], 0.6, 1.0),
    ("Sleep Tight Beds", ["bed", "nightstand", "wardrobe"], 0.5, 1.0),
    ("Entryway Goods", ["shoe rack", "coat rack", "mirror"], 0.5, 0.95),
    ("Tablecraft", ["table", "stool"], 0.5, 1.05),
    ("Storage Hub", ["cabinet", "wardrobe", "shoe rack"], 0.5, 0.95),
    ("Budget Basics", ["lamp", "chair", "bookshelf", "rug"], 0.3, 0.85),
    ("Premium Living", ["sofa", "table", "lamp", "rug"], 0.4, 1.3),
    ("City Loft Outlet", ["desk", "bookshelf", "lamp"], 0.4, 0.95),
]


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    items = large()
    write(HERE / "catalog" / "basic.json", BASIC)
    write(HERE / "catalog" / "large.json", items)
    write(HERE / "shops" / "warehouse.json", {"name": "Han's Furniture Warehouse", "catalog": "catalog/large.json",
                                              "categories": None, "sample": 1.0, "seed": 0, "price_factor": 1.0})
    for i, (name, cats, share, factor) in enumerate(SHOPS, 1):
        write(HERE / "shops" / f"shop-{i:02d}.json", {"name": name, "catalog": "catalog/large.json", "categories": cats,
                                                     "sample": share, "seed": i, "price_factor": factor})
    print(f"{len(items)} items in {len(CATEGORIES)} categories, {len(SHOPS)} shops")
