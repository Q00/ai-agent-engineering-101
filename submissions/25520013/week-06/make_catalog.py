"""Generates the large furniture catalog for challenge 1 (seeded, no download).

    uv run python make_catalog.py            # seed 0, 3000 items
    uv run python make_catalog.py --seed 1 --n 3000 --out catalog/catalog.json

Every item has an id, a name, a natural-language description that states its
dimensions and capacity, a reserve (the lowest price the shop may accept), and
a structured `attrs` dict with the same facts for machine checks. The reserve is
category base x material x size, times seeded noise.
"""

import argparse
import json
import pathlib
import random

# material -> (family, price multiplier, how the description names it)
MATERIALS = {
    "oak": ("wood", 1.25, "solid oak wood"),
    "walnut": ("wood", 1.5, "solid walnut wood"),
    "pine": ("wood", 0.8, "solid pine wood"),
    "birch": ("wood", 0.95, "birch wood"),
    "bamboo": ("wood", 0.9, "bamboo wood"),
    "teak": ("wood", 1.45, "solid teak wood"),
    "ash": ("wood", 1.1, "solid ash wood"),
    "steel": ("metal", 0.9, "powder-coated steel metal"),
    "brass": ("metal", 1.4, "solid brass metal"),
    "aluminium": ("metal", 1.0, "brushed aluminium metal"),
    "iron": ("metal", 0.85, "wrought iron metal"),
    "glass": ("glass", 1.1, "tempered glass"),
    "marble": ("marble", 1.6, "polished marble stone"),
    "ceramic": ("ceramic", 1.0, "glazed ceramic"),
    "rattan": ("rattan", 1.0, "woven rattan wicker"),
    "leather": ("leather", 1.5, "full-grain leather"),
    "fabric": ("fabric", 0.9, "woven fabric upholstery"),
    "velvet": ("fabric", 1.15, "velvet fabric upholstery"),
    "wool": ("wool", 1.3, "hand-tufted wool"),
    "cotton": ("cotton", 0.8, "flat-woven cotton"),
    "jute": ("jute", 0.85, "natural jute fibre"),
}
STYLES = [
    "Scandinavian",
    "mid-century",
    "industrial",
    "rustic",
    "modern",
    "minimalist",
    "Japanese",
    "art deco",
    "farmhouse",
    "bohemian",
]
COLOURS = [
    "natural",
    "black",
    "white",
    "grey",
    "sage green",
    "navy",
    "terracotta",
    "honey",
    "charcoal",
    "cream",
]
SYLLABLES = [
    "ha", "na", "lo", "ri", "ve", "sa", "mo", "ki", "ta", "lu",
    "ne", "ra", "vi", "do", "se", "ma", "el", "an", "or", "is",
]  # fmt: skip

WOOD = ["oak", "walnut", "pine", "birch", "bamboo", "teak", "ash"]
METAL = ["steel", "brass", "aluminium", "iron"]
SEAT = ["leather", "fabric", "velvet"]


def r5(x: float) -> int:
    """Rounds to the nearest multiple of 5."""
    return int(5 * round(x / 5))


# ---------------------------------------------------------------- categories
#
# Each category: base price, allowed materials, and a function that draws the
# dimensions and capacity with the RNG and returns (attrs, capacity sentence).


def desk_lamp(rng):
    h, w = rng.randint(30, 75), rng.randint(12, 25)
    lm = 50 * rng.randint(6, 20)
    a = dict(height_cm=h, width_cm=w, depth_cm=w, lumens=lm)
    return (
        a,
        f"A desk lamp that gives {lm} lumens of light for reading and work at a desk.",
    )


def floor_lamp(rng):
    h, w = rng.randint(140, 195), rng.randint(25, 50)
    lm = 100 * rng.randint(8, 30)
    a = dict(height_cm=h, width_cm=w, depth_cm=w, lumens=lm)
    return (
        a,
        f"A standing floor lamp that gives {lm} lumens of light to a living room corner.",
    )


def pendant_lamp(rng):
    h, w = rng.randint(20, 60), rng.randint(20, 70)
    lm = 100 * rng.randint(6, 25)
    a = dict(height_cm=h, width_cm=w, depth_cm=w, lumens=lm)
    return a, (
        f"A pendant lamp that hangs from the ceiling over a dining table and gives {lm} lumens of light."
    )


def office_chair(rng):
    h, w = rng.randint(95, 130), rng.randint(55, 70)
    kg = r5(rng.randint(100, 150))
    a = dict(height_cm=h, width_cm=w, depth_cm=rng.randint(55, 70), max_load_kg=kg)
    return a, (
        f"An adjustable office chair on wheels for long days at a desk, supporting up to {kg} kg."
    )


def dining_chair(rng):
    h, w = rng.randint(78, 100), rng.randint(40, 55)
    kg = r5(rng.randint(100, 150))
    a = dict(height_cm=h, width_cm=w, depth_cm=rng.randint(42, 55), max_load_kg=kg)
    return a, f"A dining chair for meals at the dining table, supporting up to {kg} kg."


def armchair(rng):
    h, w = rng.randint(70, 105), rng.randint(65, 95)
    sw = rng.randint(48, 65)
    a = dict(
        height_cm=h, width_cm=w, depth_cm=rng.randint(70, 95), seats=1, seat_width_cm=sw
    )
    return a, (
        f"A cosy armchair that seats one person for reading or relaxing in the living room, "
        f"with a {sw} cm wide seat."
    )


def stool(rng):
    h, w = rng.randint(45, 80), rng.randint(30, 42)
    a = dict(height_cm=h, width_cm=w, depth_cm=w, seat_height_cm=h)
    return (
        a,
        f"A backless stool with a seat {h} cm off the floor, for a kitchen counter or bar.",
    )


def bookshelf(rng):
    h, w = rng.randint(60, 220), rng.randint(40, 160)
    shelves = max(2, h // 38)
    books = r5(shelves * w / 2.6)
    a = dict(
        height_cm=h,
        width_cm=w,
        depth_cm=rng.randint(25, 40),
        shelves=shelves,
        books=books,
    )
    return a, f"A bookshelf with {shelves} shelves that holds about {books} books."


def desk(rng):
    h, w = rng.randint(72, 76), r5(rng.randint(80, 200))
    a = dict(
        height_cm=h, width_cm=w, depth_cm=r5(rng.randint(50, 80)), worktop_width_cm=w
    )
    return (
        a,
        f"A writing and computer desk with a {w} cm wide worktop for working from home.",
    )


def dining_table(rng):
    w = r5(rng.randint(80, 260))
    seats = max(2, 2 * ((w + 20) // 60))
    a = dict(height_cm=75, width_cm=w, depth_cm=r5(rng.randint(75, 105)), seats=seats)
    return a, f"A dining table for family meals that seats {seats} people."


def coffee_table(rng):
    w, h = r5(rng.randint(60, 140)), rng.randint(35, 50)
    a = dict(height_cm=h, width_cm=w, depth_cm=r5(rng.randint(40, 80)))
    return (
        a,
        f"A low coffee table to stand in front of a sofa in the living room, {w} cm long.",
    )


def sofa(rng):
    seats = rng.randint(2, 5)
    w = seats * 65 + rng.randint(20, 60)
    a = dict(
        height_cm=rng.randint(75, 95),
        width_cm=w,
        depth_cm=rng.randint(85, 105),
        seats=seats,
    )
    return a, f"A sofa couch that seats {seats} people in the living room."


def bed_frame(rng):
    name, mw = rng.choice(
        [("single", 90), ("double", 140), ("queen", 160), ("king", 180)]
    )
    a = dict(
        height_cm=rng.randint(30, 120),
        width_cm=mw + rng.randint(8, 20),
        depth_cm=200 + rng.randint(8, 20),
        mattress=name,
        mattress_width_cm=mw,
    )
    return a, f"A bed frame for a {name} mattress ({mw} x 200 cm) in the bedroom."


def wardrobe(rng):
    w = r5(rng.randint(50, 250))
    rail = w - 10
    a = dict(
        height_cm=rng.randint(170, 230),
        width_cm=w,
        depth_cm=rng.randint(50, 65),
        hanging_rail_cm=rail,
    )
    return (
        a,
        f"A wardrobe closet with a {rail} cm hanging rail for clothes in the bedroom.",
    )


def cabinet(rng):
    h = rng.randint(80, 200)
    shelves = max(2, h // 40)
    a = dict(
        height_cm=h,
        width_cm=rng.randint(40, 140),
        depth_cm=rng.randint(35, 50),
        shelves=shelves,
    )
    return (
        a,
        f"A storage cabinet with doors and {shelves} shelves inside, for a dining room or hall.",
    )


def nightstand(rng):
    drawers = rng.randint(1, 3)
    a = dict(
        height_cm=rng.randint(40, 70),
        width_cm=rng.randint(35, 60),
        depth_cm=rng.randint(30, 45),
        drawers=drawers,
    )
    return (
        a,
        f"A bedside nightstand with {drawers} drawer{'s' if drawers > 1 else ''}, to stand next to the bed.",
    )


def tv_stand(rng):
    w = r5(rng.randint(80, 220))
    tv = max(t for t in (32, 40, 43, 50, 55, 65, 75, 85) if t * 2.2 <= w or t == 32)
    a = dict(
        height_cm=rng.randint(35, 65),
        width_cm=w,
        depth_cm=rng.randint(35, 50),
        max_tv_inch=tv,
    )
    return (
        a,
        f"A TV stand media unit for televisions up to {tv} inches in the living room.",
    )


def shoe_rack(rng):
    h, w = rng.randint(40, 150), rng.randint(50, 120)
    pairs = max(4, (h // 25) * (w // 25))
    a = dict(height_cm=h, width_cm=w, depth_cm=rng.randint(25, 40), pairs=pairs)
    return a, f"A shoe rack that holds {pairs} pairs of shoes at the entrance hall."


def mirror(rng):
    h, w = rng.randint(40, 190), rng.randint(30, 100)
    full = h >= 140
    a = dict(height_cm=h, width_cm=w, depth_cm=rng.randint(2, 5), full_length=full)
    kind = "full-length mirror for checking an outfit" if full else "wall mirror"
    return a, f"A {kind} with a frame, to see yourself in; hang it on the wall or lean it against it."


def rug(rng):
    w, d = sorted((r5(rng.randint(60, 250)), r5(rng.randint(90, 350))))  # depth is the long side
    a = dict(height_cm=rng.randint(1, 3), width_cm=w, depth_cm=d)
    return a, f"A rug carpet for the floor of a living room or bedroom, {w} cm wide and {d} cm long."


CATEGORIES = {
    "desk lamp": (
        60,
        METAL + ["bamboo", "oak", "walnut", "ceramic", "glass"],
        desk_lamp,
    ),
    "floor lamp": (110, METAL + ["bamboo", "oak", "walnut", "rattan"], floor_lamp),
    "pendant lamp": (
        80,
        METAL + ["glass", "rattan", "ceramic", "bamboo"],
        pendant_lamp,
    ),
    "office chair": (150, ["leather", "fabric", "aluminium", "steel"], office_chair),
    "dining chair": (90, WOOD + ["steel", "rattan", "velvet", "leather"], dining_chair),
    "armchair": (260, SEAT + ["rattan", "oak", "walnut"], armchair),
    "stool": (50, WOOD + METAL, stool),
    "bookshelf": (160, WOOD + ["steel", "iron"], bookshelf),
    "desk": (200, WOOD + ["steel", "glass"], desk),
    "dining table": (320, WOOD + ["glass", "marble"], dining_table),
    "coffee table": (140, WOOD + ["glass", "marble", "rattan", "iron"], coffee_table),
    "sofa": (600, SEAT, sofa),
    "bed frame": (380, WOOD + ["iron", "fabric", "velvet"], bed_frame),
    "wardrobe": (420, WOOD + ["steel"], wardrobe),
    "cabinet": (260, WOOD + ["steel", "glass"], cabinet),
    "nightstand": (90, WOOD + ["rattan", "steel"], nightstand),
    "tv stand": (180, WOOD + ["steel", "glass"], tv_stand),
    "shoe rack": (60, WOOD + METAL, shoe_rack),
    "mirror": (90, ["oak", "walnut", "brass", "steel", "rattan", "iron"], mirror),
    "rug": (150, ["wool", "cotton", "jute"], rug),
}


def model_name(rng) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 3))).capitalize()


def make_item(rng, idx: int, category: str) -> dict:
    base, materials, draw = CATEGORIES[category]
    material = rng.choice(materials)
    family, mult, phrase = MATERIALS[material]
    style, colour = rng.choice(STYLES), rng.choice(COLOURS)
    dims, use = draw(rng)
    attrs = {
        "category": category,
        "material": material,
        "material_family": family,
        "style": style,
        "colour": colour,
        **dims,
    }
    # Size factor relative to the category's median volume; dampened so a big piece costs more, not 10x more.
    volume = dims["height_cm"] * dims["width_cm"] * dims["depth_cm"]
    size = (volume / REF_VOLUME[category]) ** 0.3
    reserve = max(
        20, round(base * mult * min(max(size, 0.6), 1.8) * rng.uniform(0.85, 1.15))
    )
    title = category.title().replace("Tv", "TV")
    name = f"{model_name(rng)} {style.title()} {material.title()} {title}"
    description = (
        f"{use} {style.capitalize()} style in {phrase}, {colour} finish. "
        f"{dims['height_cm']} cm high, {dims['width_cm']} cm wide, {dims['depth_cm']} cm deep."
    )
    return {
        "id": f"C{idx:05d}",
        "name": name,
        "description": description,
        "reserve": reserve,
        "attrs": attrs,
    }


def reference_volumes(seed: int) -> dict:
    """Median volume per category, from a separate draw so reserves are scale-free."""
    rng = random.Random(seed + 10_000)
    ref = {}
    for cat, (_, _, draw) in CATEGORIES.items():
        vols = sorted(
            d["height_cm"] * d["width_cm"] * d["depth_cm"]
            for d in (draw(rng)[0] for _ in range(201))
        )
        ref[cat] = vols[100]
    return ref


REF_VOLUME: dict = {}


def make_catalog(seed: int = 0, n: int = 3000) -> list[dict]:
    REF_VOLUME.update(reference_volumes(seed))
    rng = random.Random(seed)
    cats = list(CATEGORIES)
    return [make_item(rng, i, cats[i % len(cats)]) for i in range(n)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--out", default="catalog/catalog.json")
    args = ap.parse_args()
    items = make_catalog(args.seed, args.n)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    print(
        f"seed {args.seed}: {len(items)} items, {len(CATEGORIES)} categories -> {out} ({out.stat().st_size // 1024} KB)"
    )
