"""Week 06, challenge 1 -- build the shop's catalogue.

Thousands of items, each with a name, a description and a reserve, generated
from a fixed seed so the file can be rebuilt byte for byte:

    python make_catalog.py          # writes catalog.json

Two things the generator has to get right.

    The descriptions have to be worth searching. A buyer who says "something
    wooden to keep about 40 books, not taller than my desk" can only be served
    if the catalogue records material, capacity and height in words, so every
    line carries them.

    The three items the trade test buys -- lamp 100, chair 180, bookshelf 260
    -- are in the file under those exact names and reserves. The catalogue is
    an addition to the shop, not a replacement for it, and a run of the trade
    test has to come out the same as before.
"""
import json
import random

random.seed(20260119)

# category -> (base reserve, words that belong to it)
CATEGORY = {
    "bookshelf": (180, ["shelving", "bookcase"]),
    "lamp": (60, ["light", "lighting"]),
    "chair": (120, ["seat", "seating"]),
    "desk": (200, ["workstation", "writing table"]),
    "table": (170, ["dining table"]),
    "sofa": (380, ["couch", "settee"]),
    "wardrobe": (420, ["closet", "armoire"]),
    "cabinet": (240, ["sideboard", "storage"]),
    "stool": (55, ["seat"]),
    "bench": (140, ["seating"]),
    "mirror": (90, ["looking glass"]),
    "rug": (110, ["carpet", "floor covering"]),
    "shelf": (70, ["wall shelf", "ledge"]),
    "nightstand": (95, ["bedside table"]),
    "coffee table": (150, ["low table"]),
}

MATERIAL = {
    "oak": 1.35, "walnut": 1.55, "pine": 0.85, "birch": 1.0, "teak": 1.6,
    "bamboo": 0.9, "rattan": 1.05, "steel": 1.15, "aluminium": 1.1,
    "glass": 1.25, "marble": 1.9, "plastic": 0.6, "plywood": 0.75,
}

STYLE = ["modern", "classic", "rustic", "industrial", "scandinavian",
         "minimalist", "mid-century", "farmhouse", "art-deco"]

ROOM = ["living room", "bedroom", "study", "kitchen", "hallway", "office",
        "dining room", "nursery"]

FINISH = ["matte", "gloss", "oiled", "waxed", "brushed", "lacquered",
          "whitewashed", "stained"]


def sentence(cat, material, style, room, finish, h, w, cap):
    """One searchable line: what it is, what it is made of, how big, what it holds."""
    bits = [f"A {style} {material} {cat} for the {room}, {finish} finish."]
    bits.append(f"It stands {h} cm tall and {w} cm wide.")
    if cap:
        if cat in ("bookshelf", "shelf", "cabinet"):
            bits.append(f"Holds about {cap} books across its shelves.")
        elif cat in ("sofa", "bench"):
            bits.append(f"Seats {cap} people.")
        elif cat == "wardrobe":
            bits.append(f"Fits about {cap} hanging garments.")
    bits.append("Also listed as " + ", ".join(CATEGORY[cat][1]) + ".")
    return " ".join(bits)


def build():
    items = []
    # The three the trade test asks for, by name, at the reserves it checks.
    for name, reserve, desc in (
        ("lamp", 100, "A plain desk lamp. Steel base, fabric shade, 45 cm tall. "
                      "Also listed as light, lighting."),
        ("chair", 180, "A plain dining chair. Birch frame, padded seat, 90 cm tall. "
                       "Also listed as seat, seating."),
        ("bookshelf", 260, "A plain bookshelf. Pine, five shelves, 180 cm tall, "
                           "holds about 120 books. Also listed as shelving, bookcase."),
    ):
        items.append({"id": name, "name": name, "category": name,
                      "material": "", "description": desc, "reserve": reserve})

    n = 0
    for cat, (base, _) in CATEGORY.items():
        for material, mult in MATERIAL.items():
            for style in STYLE:
                for finish in random.sample(FINISH, 2):
                    n += 1
                    room = random.choice(ROOM)
                    h = random.choice([35, 45, 60, 75, 90, 110, 140, 180, 210])
                    w = random.choice([30, 45, 60, 80, 100, 120, 160, 200])
                    cap = random.choice([10, 20, 30, 40, 60, 80, 120]) \
                        if cat in ("bookshelf", "shelf", "cabinet") else \
                        random.choice([2, 3, 4]) if cat in ("sofa", "bench") else \
                        random.choice([15, 25, 40]) if cat == "wardrobe" else 0
                    reserve = int(base * mult * random.uniform(0.85, 1.25))
                    items.append({
                        "id": f"itm-{n:05d}",
                        "name": f"{style.title()} {material.title()} {cat.title()}",
                        "category": cat,
                        "material": material,
                        "description": sentence(cat, material, style, room, finish, h, w, cap),
                        "reserve": max(20, reserve),
                    })
    return items


if __name__ == "__main__":
    items = build()
    with open("catalog.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"catalog.json: {len(items)} items, "
          f"{len({i['category'] for i in items})} categories")
