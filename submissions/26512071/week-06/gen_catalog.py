"""Generate catalog.json: a few thousand furniture items for challenge 1.

    python gen_catalog.py            # writes catalog.json next to this file

Items are combinations of kind x material x style x size, with a seeded RNG so the
file is reproducible. Each item has a name, a description with the numbers a buyer
might ask about (height, width, how many books, how many people), and a reserve.
The three items the trade test asks for by name (lamp, chair, bookshelf) are fixed
at the top with reserves 100, 180 and 260.
"""
import json
import random
from pathlib import Path

SEED = 6
FIXED = [
    {"id": "lamp", "name": "lamp", "reserve": 100,
     "description": "A plain desk lamp with a metal arm and a warm LED bulb. 45 cm tall."},
    {"id": "chair", "name": "chair", "reserve": 180,
     "description": "A plain dining chair in beech wood with a straight back. Seat 46 cm high."},
    {"id": "bookshelf", "name": "bookshelf", "reserve": 260,
     "description": "A plain five-shelf bookshelf in pine. 180 cm tall, holds about 150 books."},
]

# kind: (base reserve, height range cm, width range cm, what it is for)
KINDS = {
    "bookcase":      (220, (60, 200), (40, 120), "storage for books and binders"),
    "wall shelf":    (60,  (15, 40),  (40, 120), "a shelf that hangs on the wall"),
    "cube organizer": (90, (30, 150), (30, 110), "square cubbies for books, boxes or toys"),
    "desk lamp":     (70,  (30, 60),  (12, 25),  "light for reading or working at a desk"),
    "floor lamp":    (110, (140, 190), (25, 45), "a standing light for a corner of the room"),
    "office chair":  (160, (90, 125), (55, 70),  "a swivel chair for long hours at a desk"),
    "stool":         (45,  (45, 80),  (30, 40),  "a backless seat for a counter or a small space"),
    "armchair":      (280, (80, 105), (70, 95),  "an upholstered seat for one person to relax"),
    "sofa":          (550, (75, 95),  (150, 240), "a couch for the living room"),
    "desk":          (240, (72, 76),  (90, 180), "a table to work or study at"),
    "side table":    (70,  (45, 65),  (35, 55),  "a small table next to a sofa or bed"),
    "dining table":  (380, (73, 77),  (120, 220), "a table for meals"),
    "dresser":       (300, (70, 120), (70, 160), "drawers for clothes"),
    "wardrobe":      (420, (170, 210), (80, 200), "a tall closet for hanging clothes"),
    "bed frame":     (350, (30, 120), (90, 180), "a frame that holds a mattress"),
    "tv stand":      (150, (40, 60),  (100, 200), "a low cabinet for a television"),
    "coat rack":     (40,  (160, 185), (40, 60), "hooks for coats and hats by the door"),
    "shoe cabinet":  (110, (80, 130), (60, 100), "closed storage for shoes in the entryway"),
}
MATERIALS = {"pine": 1.0, "oak": 1.5, "walnut": 1.8, "birch": 1.2, "bamboo": 1.1,
             "steel": 1.1, "aluminium": 1.2, "particleboard": 0.7, "rattan": 1.3, "plastic": 0.6}
WOODEN = {"pine", "oak", "walnut", "birch", "bamboo", "rattan"}
STYLES = ["Scandinavian", "industrial", "mid-century", "minimalist", "rustic", "modern", "vintage", "Japanese"]
COLORS = ["white", "black", "natural", "grey", "dark brown", "green", "blue", "beige"]


def extra(kind, h, w, rng):
    """One sentence with the numbers that matter for this kind of item."""
    if kind in ("bookcase", "wall shelf", "cube organizer"):
        books = max(5, int(w / 2.5 * max(1, h // 35)))
        return f"Holds about {books} books."
    if kind in ("office chair", "stool", "armchair"):
        return f"Seat height {rng.randint(42, 75) if kind == 'stool' else rng.randint(42, 50)} cm."
    if kind == "sofa":
        return f"Seats {max(2, w // 60)} people."
    if kind in ("desk", "dining table"):
        return f"Top is {w} x {rng.randint(50, 100)} cm" + (f", seats {max(2, w // 50)}." if kind == "dining table" else ".")
    if kind in ("desk lamp", "floor lamp"):
        return f"{rng.choice(['Warm', 'Cool', 'Dimmable'])} LED, {rng.choice([400, 600, 800, 1000])} lumens."
    if kind == "bed frame":
        return f"Fits a {rng.choice(['single', 'double', 'queen', 'king'])} mattress."
    return f"{rng.randint(2, 6)} compartments."


def generate(n=3000):
    rng = random.Random(SEED)
    items, seen = list(FIXED), {i["name"] for i in FIXED}
    while len(items) < n:
        kind = rng.choice(list(KINDS))
        base, (h0, h1), (w0, w1), purpose = KINDS[kind]
        material, style, color = rng.choice(list(MATERIALS)), rng.choice(STYLES), rng.choice(COLORS)
        h, w = rng.randint(h0, h1), rng.randint(w0, w1)
        name = f"{style} {color} {material} {kind} {h}x{w}"
        if name in seen:
            continue
        seen.add(name)
        size = (h * w) / ((h0 + h1) / 2 * (w0 + w1) / 2)  # 1.0 = an average one of its kind
        reserve = int(base * MATERIALS[material] * (0.7 + 0.3 * size) * rng.uniform(0.9, 1.1))
        feel = "solid wood" if material in WOODEN and material != "rattan" else material
        article = "An" if style[0] in "AEIOUaeiou" else "A"
        desc = (f"{article} {style} {kind} in {color} {feel}, {purpose}. "
                f"{h} cm tall, {w} cm wide. {extra(kind, h, w, rng)}")
        items.append({"id": f"item-{len(items):04d}", "name": name, "reserve": max(10, reserve), "description": desc})
    return items


if __name__ == "__main__":
    out = Path(__file__).with_name("catalog.json")
    items = generate()
    out.write_text(json.dumps(items, indent=0, ensure_ascii=False))
    print(f"wrote {len(items)} items to {out.name}")
