"""Generates the seller's catalog: a few thousand furniture items with a name, a description and a reserve.

    python gen_catalog.py                       # catalog.json (seed 6)
    python gen_catalog.py --seed 7 --out catalogs/shop-07.json --price 1.15 --only lamp,chair

Deterministic for a given seed. The first three items are the trade test's
lamp, chair and bookshelf with reserves 100, 180 and 260; every catalog keeps them.
"""
import argparse
import json
import random

BASE = [
    {"id": "lamp", "name": "lamp", "category": "lamp", "reserve": 100,
     "description": "A plain desk lamp with a steel arm and a warm LED bulb, 45 cm tall."},
    {"id": "chair", "name": "chair", "category": "chair", "reserve": 180,
     "description": "A plain wooden dining chair in beech, seat height 46 cm."},
    {"id": "bookshelf", "name": "bookshelf", "category": "bookshelf", "reserve": 260,
     "description": "A plain pine bookshelf, 180 cm tall, five shelves, holds about 150 books."},
]

MATERIALS = {  # name -> (price factor, words that a buyer might use)
    "solid oak": (1.5, "wood wooden hardwood"),
    "walnut": (1.7, "wood wooden hardwood dark"),
    "pine": (0.9, "wood wooden softwood light"),
    "birch plywood": (1.0, "wood wooden plywood light"),
    "teak": (1.8, "wood wooden hardwood outdoor"),
    "bamboo": (0.95, "wood wooden natural eco"),
    "white MDF": (0.7, "white painted cheap"),
    "black steel": (1.1, "metal steel industrial black"),
    "brushed aluminium": (1.2, "metal aluminium silver light"),
    "rattan": (1.1, "natural woven wicker boho"),
    "glass": (1.3, "glass transparent modern"),
    "velvet": (1.4, "fabric soft upholstered"),
    "linen": (1.2, "fabric soft upholstered natural"),
    "leather": (1.9, "leather upholstered premium"),
}
STYLES = ["Nordic", "Industrial", "Mid-century", "Minimal", "Rustic", "Classic", "Compact", "Studio",
          "Loft", "Coastal", "Urban", "Heritage"]


def heights(lo, hi, step):
    return list(range(lo, hi + 1, step))


# category -> (base reserve, materials allowed, description builder)
def bookshelf(rng, h):
    width = rng.choice([40, 60, 80, 100, 120])
    shelves = max(1, h // 35)
    books = shelves * width // 3
    return (f"{shelves} shelves, {h} cm tall, {width} cm wide, holds about {books} books.",
            {"height_cm": h, "width_cm": width, "books": books}, 0.6 + h / 180 + width / 200)


def cube_shelf(rng, h):
    cubes = rng.choice([2, 4, 6, 8, 9])
    books = cubes * 12
    return (f"Cube storage with {cubes} open cubes, {h} cm tall, fits about {books} books or boxes.",
            {"height_cm": h, "books": books}, 0.5 + cubes / 8)


def lamp_desk(rng, h):
    return (f"Desk lamp, {h} cm tall, adjustable arm, {rng.choice(['warm', 'neutral', 'cool'])} light.",
            {"height_cm": h}, 0.7 + h / 100)


def lamp_floor(rng, h):
    return (f"Floor lamp, {h} cm tall, {rng.choice(['arc', 'tripod', 'torchiere', 'straight'])} shape, dimmable.",
            {"height_cm": h}, 0.8 + h / 200)


def lamp_pendant(rng, h):
    d = rng.choice([25, 35, 45, 60])
    return (f"Pendant ceiling lamp, shade {d} cm across, cable up to {h} cm.", {"shade_cm": d}, 0.6 + d / 60)


def chair_dining(rng, h):
    return (f"Dining chair, seat height {rng.choice([44, 45, 46, 47])} cm, {h} cm tall overall.",
            {"height_cm": h}, 1.0)


def chair_office(rng, h):
    return (f"Office chair with wheels, adjustable seat, {rng.choice(['mesh', 'padded'])} back, {h} cm tall.",
            {"height_cm": h}, 1.3)


def armchair(rng, h):
    return (f"Armchair for reading, {h} cm tall, deep seat.", {"height_cm": h}, 1.6)


def stool(rng, h):
    return (f"Stool, {h} cm tall, {rng.choice(['bar', 'kitchen', 'step', 'low'])} use.", {"height_cm": h}, 0.6)


def desk(rng, h):
    w = rng.choice([90, 110, 120, 140, 160])
    return (f"Desk, {w} cm wide, {h} cm tall, {rng.choice(['one drawer', 'two drawers', 'no drawers', 'cable tray'])}.",
            {"height_cm": h, "width_cm": w}, 0.8 + w / 150)


def table(rng, h):
    seats = rng.choice([2, 4, 6, 8])
    return (f"Dining table for {seats}, {h} cm tall.", {"height_cm": h, "seats": seats}, 0.7 + seats / 5)


def coffee_table(rng, h):
    return (f"Coffee table, {h} cm tall, {rng.choice(['round', 'oval', 'rectangular', 'square'])} top.",
            {"height_cm": h}, 0.8)


def cabinet(rng, h):
    doors = rng.choice([1, 2, 3])
    return (f"Storage cabinet with {doors} doors and shelves inside, {h} cm tall.", {"height_cm": h}, 0.8 + h / 150)


def drawers(rng, h):
    n = rng.choice([3, 4, 5, 6])
    return (f"Chest of {n} drawers, {h} cm tall.", {"height_cm": h, "drawers": n}, 0.7 + n / 6)


def nightstand(rng, h):
    return (f"Nightstand with {rng.choice(['one drawer', 'two drawers', 'an open shelf'])}, {h} cm tall.",
            {"height_cm": h}, 0.7)


def tv_stand(rng, h):
    w = rng.choice([100, 120, 150, 180])
    return (f"TV stand, {w} cm wide, {h} cm tall, for screens up to {w // 2 + 10} inches.",
            {"height_cm": h, "width_cm": w}, 0.7 + w / 200)


def shoe_rack(rng, h):
    pairs = rng.choice([6, 9, 12, 16, 20])
    return (f"Shoe rack for {pairs} pairs, {h} cm tall.", {"height_cm": h, "pairs": pairs}, 0.5 + pairs / 30)


def coat_rack(rng, h):
    return (f"Standing coat rack with {rng.choice([6, 8, 10])} hooks, {h} cm tall.", {"height_cm": h}, 0.6)


def wardrobe(rng, h):
    return (f"Wardrobe with hanging rail and shelf, {h} cm tall.", {"height_cm": h}, 1.0 + h / 200)


def sofa(rng, h):
    seats = rng.choice([2, 3, 4])
    return (f"Sofa for {seats}, {h} cm tall at the back.", {"height_cm": h, "seats": seats}, 1.0 + seats / 3)


def bed(rng, h):
    size = rng.choice(["single", "double", "queen", "king"])
    return (f"{size.capitalize()} bed frame, headboard {h} cm tall.", {"height_cm": h, "size": size},
            {"single": 1.0, "double": 1.3, "queen": 1.5, "king": 1.8}[size])


def mirror(rng, h):
    return (f"{rng.choice(['Wall', 'Standing', 'Round wall'])} mirror, {h} cm tall.", {"height_cm": h}, 0.6 + h / 200)


def rug(rng, h):
    w, l = rng.choice([(80, 150), (120, 170), (160, 230), (200, 300)])
    return (f"Rug, {w} by {l} cm, {rng.choice(['low pile', 'flatweave', 'shaggy'])}.", {"size_cm": [w, l]}, 0.5 + w / 200)


CATEGORIES = {  # category -> (builder, base reserve, heights, materials)
    "bookshelf": (bookshelf, 200, heights(60, 200, 10), ["solid oak", "walnut", "pine", "birch plywood", "bamboo",
                                                          "white MDF", "black steel"]),
    "cube shelf": (cube_shelf, 120, heights(40, 160, 20), ["pine", "birch plywood", "white MDF", "bamboo"]),
    "desk lamp": (lamp_desk, 70, heights(30, 70, 5), ["black steel", "brushed aluminium", "solid oak", "glass", "bamboo"]),
    "floor lamp": (lamp_floor, 120, heights(140, 190, 10), ["black steel", "brushed aluminium", "solid oak", "rattan", "linen"]),
    "pendant lamp": (lamp_pendant, 90, heights(100, 200, 50), ["rattan", "glass", "black steel", "bamboo", "linen"]),
    "dining chair": (chair_dining, 140, heights(78, 95, 3), ["solid oak", "walnut", "pine", "black steel", "rattan",
                                                              "velvet", "linen", "leather"]),
    "office chair": (chair_office, 220, heights(95, 125, 5), ["black steel", "brushed aluminium", "leather", "linen"]),
    "armchair": (armchair, 380, heights(80, 105, 5), ["velvet", "linen", "leather", "rattan"]),
    "stool": (stool, 80, heights(25, 80, 5), ["solid oak", "pine", "bamboo", "black steel", "rattan"]),
    "desk": (desk, 260, heights(70, 76, 2), ["solid oak", "walnut", "pine", "birch plywood", "white MDF", "black steel"]),
    "dining table": (table, 400, heights(72, 76, 2), ["solid oak", "walnut", "pine", "teak", "glass"]),
    "coffee table": (coffee_table, 180, heights(30, 50, 5), ["solid oak", "walnut", "glass", "rattan", "black steel"]),
    "cabinet": (cabinet, 300, heights(60, 200, 20), ["solid oak", "walnut", "pine", "birch plywood", "white MDF"]),
    "chest of drawers": (drawers, 320, heights(70, 130, 10), ["solid oak", "walnut", "pine", "white MDF"]),
    "nightstand": (nightstand, 110, heights(45, 65, 5), ["solid oak", "walnut", "pine", "white MDF", "rattan"]),
    "tv stand": (tv_stand, 230, heights(40, 60, 5), ["solid oak", "walnut", "birch plywood", "white MDF", "black steel"]),
    "shoe rack": (shoe_rack, 70, heights(40, 120, 10), ["bamboo", "pine", "black steel", "white MDF"]),
    "coat rack": (coat_rack, 60, heights(160, 190, 10), ["solid oak", "bamboo", "black steel"]),
    "wardrobe": (wardrobe, 450, heights(160, 220, 10), ["solid oak", "pine", "birch plywood", "white MDF"]),
    "sofa": (sofa, 700, heights(70, 95, 5), ["velvet", "linen", "leather"]),
    "bed frame": (bed, 500, heights(90, 130, 10), ["solid oak", "walnut", "pine", "black steel", "velvet"]),
    "mirror": (mirror, 90, heights(50, 180, 10), ["solid oak", "black steel", "rattan", "brushed aluminium"]),
    "rug": (rug, 120, [1], ["linen", "velvet", "rattan"]),
}


def generate(seed, per_category, price=1.0, only=None):
    rng = random.Random(seed)
    items, seen = [dict(b) for b in BASE], set()
    for cat, (build, base, hs, mats) in CATEGORIES.items():
        if only and not any(o in cat for o in only):
            continue
        for _ in range(per_category):
            mat, style, h = rng.choice(mats), rng.choice(STYLES), rng.choice(hs)
            text, attrs, size = build(rng, h)
            factor, words = MATERIALS[mat]
            reserve = int(round(base * factor * size * price * rng.uniform(0.9, 1.1) / 5) * 5)
            name = f"{style} {mat} {cat}"
            n = 2
            while name in seen:
                name, n = f"{style} {mat} {cat} {n}", n + 1
            seen.add(name)
            items.append({"id": f"{cat.replace(' ', '-')}-{len(items):04d}", "name": name, "category": cat,
                          "reserve": max(reserve, 20), "material": mat,
                          "description": f"{style} {cat} in {mat} ({words}). {text}", **attrs})
    return items


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--per-category", type=int, default=130)
    ap.add_argument("--price", type=float, default=1.0, help="multiply every generated reserve")
    ap.add_argument("--only", help="comma-separated category words, e.g. lamp,chair")
    ap.add_argument("--out", default="catalog.json")
    a = ap.parse_args()
    items = generate(a.seed, a.per_category, a.price, a.only.split(",") if a.only else None)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=0)
    print(f"{len(items)} items -> {a.out}")
