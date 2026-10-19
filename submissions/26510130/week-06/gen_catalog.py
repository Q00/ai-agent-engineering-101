"""Generate the seller's catalog for challenge 1.

Deterministic (fixed seed), so the committed catalog.json can be regenerated
and checked. The three items the trade test uses keep exactly the reserves the
assignment fixes -- lamp 100, chair 180, bookshelf 260 -- and are written
first, so a buyer that names one of them by word hits them.

    python gen_catalog.py            # writes catalog.json
"""
import itertools
import json
import random
from pathlib import Path

SEED = 26510130
TARGET = 3000

FIXED = [
    {"id": "it-0001", "name": "lamp", "reserve": 100,
     "description": "A desk lamp with an adjustable arm and a warm LED bulb. Small footprint, "
                    "clamps or stands on a desk, good for reading and close work."},
    {"id": "it-0002", "name": "chair", "reserve": 180,
     "description": "An office chair with a padded seat, height adjustment and castors. "
                    "Seats one person at a desk for long stretches."},
    {"id": "it-0003", "name": "bookshelf", "reserve": 260,
     "description": "A wooden bookshelf, five shelves, about 180 cm tall and 80 cm wide. "
                    "Holds roughly 150 books, stands against a wall."},
]

MATERIAL = ["oak", "walnut", "pine", "birch", "bamboo", "steel", "powder-coated steel",
            "aluminium", "tempered glass", "ceramic", "linen", "felt", "cork", "rattan"]
FINISH = ["matte", "satin", "brushed", "oiled", "lacquered", "raw", "whitewashed"]
ROOM = ["study", "bedroom", "living room", "home office", "kitchen", "workshop",
        "hallway", "studio", "dorm room", "balcony"]

KIND = [
    # (noun, plural-ish descriptor, base reserve, what it is for)
    ("shelf unit", "shelving", 150, "holds books and boxes against a wall"),
    ("low bookcase", "shelving", 120, "holds about 40 books and stays below desk height"),
    ("tall bookcase", "shelving", 300, "holds about 200 books, floor to near ceiling"),
    ("wall shelf", "shelving", 60, "a single shelf fixed to the wall"),
    ("desk", "tables", 220, "a work surface for a laptop and papers"),
    ("side table", "tables", 90, "a small table beside a sofa or bed"),
    ("dining table", "tables", 420, "seats four to six people for meals"),
    ("stool", "seating", 70, "backless seating for a counter or workbench"),
    ("armchair", "seating", 340, "a padded chair with arms for reading"),
    ("bench", "seating", 160, "seats two or three people in a hallway"),
    ("floor lamp", "lighting", 130, "stands on the floor and lights a corner"),
    ("pendant lamp", "lighting", 110, "hangs from the ceiling over a table"),
    ("reading light", "lighting", 55, "a small clip-on light for a book"),
    ("storage box", "storage", 40, "stacks and keeps small things out of sight"),
    ("filing cabinet", "storage", 200, "two or three drawers for documents"),
    ("coat rack", "storage", 80, "hangs coats and bags near a door"),
    ("rug", "soft furnishing", 150, "covers a floor area and softens a room"),
    ("cushion", "soft furnishing", 30, "a seat or back cushion"),
    ("curtain pair", "soft furnishing", 95, "two panels for one window"),
    ("mirror", "decor", 120, "a framed mirror for a wall"),
    ("planter", "decor", 45, "holds a medium indoor plant"),
    ("clock", "decor", 65, "a wall clock with a quiet movement"),
]

SIZE = [("compact", 0.75), ("standard", 1.0), ("wide", 1.25), ("extra-large", 1.6)]


def main():
    rng = random.Random(SEED)
    items = [dict(f, category="core", material="", size="standard") for f in FIXED]
    seen = {i["name"] for i in items}
    n = len(items)

    combos = list(itertools.product(KIND, MATERIAL, SIZE, FINISH))
    rng.shuffle(combos)
    for (kind, cat, base, purpose), material, (size, mult), finish in combos:
        if n >= TARGET:
            break
        name = f"{finish} {material} {kind}" if size == "standard" else f"{size} {finish} {material} {kind}"
        if name in seen:
            continue
        seen.add(name)
        n += 1
        reserve = max(20, int(round(base * mult * rng.uniform(0.85, 1.2) / 5) * 5))
        room = rng.choice(ROOM)
        items.append({
            "id": f"it-{n:04d}",
            "name": name,
            "category": cat,
            "material": material,
            "size": size,
            "reserve": reserve,
            "description": f"A {size} {kind} in {finish} {material}. It {purpose}. "
                           f"Suits a {room}.",
        })

    Path("catalog.json").write_text(
        json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote catalog.json: {len(items)} items, "
          f"reserves {min(i['reserve'] for i in items)}-{max(i['reserve'] for i in items)}")
    for f in FIXED:
        got = next(i for i in items if i["name"] == f["name"])
        assert got["reserve"] == f["reserve"], got
    print("fixed reserves intact: lamp 100, chair 180, bookshelf 260")


if __name__ == "__main__":
    main()
