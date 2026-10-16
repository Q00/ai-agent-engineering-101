"""Build the catalogs: one large catalog for challenge 1 and twenty shop catalogs for challenge 2.

    python make_catalog.py          # writes catalog.json and shops/shop-01.json .. shop-20.json

Deterministic (fixed seed), so the files in the repo can be rebuilt byte for byte.

Every item has an id, a name, a description, a reserve (lowest acceptable price) and the attributes
the description was written from (`attrs`), which the challenge drivers use to check that a sale
matched what the buyer described. The three trade-test items keep their exact names and reserves:
`lamp` 100, `chair` 180, `bookshelf` 260.
"""
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 6
N_ITEMS = 3000
N_SHOPS = 20

WOODS = ["oak", "pine", "walnut", "birch", "teak", "beech"]
METALS = ["steel", "brass", "aluminium", "iron"]
OTHER = ["bamboo", "rattan", "plastic", "glass", "ceramic", "fabric", "leather", "marble"]
COLORS = ["black", "white", "grey", "natural", "navy", "green", "red", "cream", "brown", "yellow"]
MODELS = ["Arvo", "Brisa", "Calla", "Dorin", "Elva", "Fjord", "Gala", "Halden", "Iris", "Juno", "Kasa",
          "Lumo", "Mira", "Nord", "Orla", "Pavo", "Quill", "Runa", "Sola", "Tarn", "Ulla", "Vesta",
          "Wren", "Yara", "Zola", "Alto", "Bento", "Cove", "Dune", "Ember"]

# kind -> (category, materials, base price range, attribute maker)
def lamp_attrs(r, kind):
    h = {"desk lamp": r.randint(30, 60), "floor lamp": r.randint(140, 185), "reading lamp": r.randint(40, 150),
         "pendant lamp": r.randint(20, 45), "bedside lamp": r.randint(25, 50)}[kind]
    return {"height_cm": h, "dimmable": r.random() < 0.5, "bulb": r.choice(["LED", "E27", "E14", "GU10"])}


def chair_attrs(r, kind):
    return {"height_cm": r.randint(70, 120) if kind != "stool" else r.randint(45, 80),
            "wheels": kind == "office chair" and r.random() < 0.85,
            "adjustable": kind in ("office chair", "stool") and r.random() < 0.7,
            "armrests": kind in ("office chair", "armchair") or r.random() < 0.2,
            "max_load_kg": r.choice([100, 120, 136, 150])}


def shelf_attrs(r, kind):
    h = r.randint(60, 210) if kind == "bookshelf" else r.randint(30, 120)
    shelves = max(2, h // 35)
    return {"height_cm": h, "width_cm": r.choice([40, 60, 80, 100, 120]), "shelves": shelves,
            "holds_books": shelves * r.choice([10, 15, 20, 25])}


def table_attrs(r, kind):
    h = {"desk": r.randint(70, 78), "standing desk": r.randint(70, 125), "coffee table": r.randint(35, 50),
         "dining table": r.randint(73, 78), "side table": r.randint(45, 65), "nightstand": r.randint(45, 65)}[kind]
    return {"height_cm": h, "width_cm": r.choice([40, 50, 60, 80, 100, 120, 140, 160, 180]),
            "seats": r.choice([2, 4, 6, 8]) if kind == "dining table" else 0,
            "drawers": r.choice([0, 0, 1, 2, 3])}


def sofa_attrs(r, kind):
    return {"seats": {"sofa": r.choice([2, 3, 4]), "loveseat": 2, "sofa bed": r.choice([2, 3])}[kind],
            "width_cm": r.randint(140, 260), "sleeper": kind == "sofa bed"}


def storage_attrs(r, kind):
    return {"height_cm": r.randint(40, 200), "width_cm": r.choice([40, 60, 80, 100, 120]),
            "doors": r.choice([0, 1, 2, 3]), "drawers": r.choice([0, 2, 3, 4, 5])}


def bed_attrs(r, kind):
    return {"size": r.choice(["single", "double", "queen", "king"]), "storage": r.random() < 0.3,
            "height_cm": r.randint(25, 45)}


def decor_attrs(r, kind):
    return {"width_cm": r.choice([40, 60, 80, 120, 160, 200, 240]),
            "height_cm": r.choice([40, 60, 80, 120, 160, 180])}


KINDS = {
    "desk lamp": ("lighting", WOODS[:2] + METALS + ["ceramic", "plastic"], (25, 110), lamp_attrs),
    "floor lamp": ("lighting", METALS + WOODS[:3] + ["rattan"], (60, 220), lamp_attrs),
    "reading lamp": ("lighting", METALS + ["plastic", "ceramic"], (30, 140), lamp_attrs),
    "pendant lamp": ("lighting", METALS + ["glass", "rattan", "bamboo"], (35, 180), lamp_attrs),
    "bedside lamp": ("lighting", ["ceramic", "glass", "brass", "oak", "fabric"], (20, 90), lamp_attrs),
    "office chair": ("seating", ["fabric", "leather", "plastic", "steel"], (90, 420), chair_attrs),
    "dining chair": ("seating", WOODS + ["plastic", "steel", "rattan"], (40, 220), chair_attrs),
    "armchair": ("seating", ["fabric", "leather", "rattan"], (150, 650), chair_attrs),
    "folding chair": ("seating", ["steel", "plastic", "beech", "aluminium"], (15, 70), chair_attrs),
    "stool": ("seating", WOODS + METALS + ["plastic"], (20, 140), chair_attrs),
    "bookshelf": ("storage", WOODS + ["steel", "bamboo"], (60, 480), shelf_attrs),
    "wall shelf": ("storage", WOODS + METALS + ["glass"], (15, 120), shelf_attrs),
    "cabinet": ("storage", WOODS + ["steel", "glass"], (90, 520), storage_attrs),
    "wardrobe": ("storage", WOODS + ["steel"], (180, 900), storage_attrs),
    "shoe rack": ("storage", ["bamboo", "steel", "pine", "plastic"], (20, 110), storage_attrs),
    "desk": ("tables", WOODS + ["steel", "glass"], (90, 520), table_attrs),
    "standing desk": ("tables", ["oak", "birch", "bamboo", "steel"], (220, 900), table_attrs),
    "coffee table": ("tables", WOODS + ["glass", "marble"], (50, 420), table_attrs),
    "dining table": ("tables", WOODS + ["glass", "marble"], (150, 1100), table_attrs),
    "side table": ("tables", WOODS + METALS + ["marble"], (30, 200), table_attrs),
    "nightstand": ("tables", WOODS + ["rattan"], (40, 260), table_attrs),
    "sofa": ("sofas", ["fabric", "leather"], (350, 2200), sofa_attrs),
    "loveseat": ("sofas", ["fabric", "leather"], (250, 1200), sofa_attrs),
    "sofa bed": ("sofas", ["fabric"], (300, 1400), sofa_attrs),
    "bed frame": ("beds", WOODS + METALS[:2], (180, 1400), bed_attrs),
    "rug": ("decor", ["wool", "cotton", "jute", "polyester"], (30, 600), decor_attrs),
    "mirror": ("decor", ["glass", "brass", "oak"], (25, 320), decor_attrs),
}

PHRASES = {
    "lighting": ["Gives a warm, even light.", "Good for reading at night.", "A soft glow for small rooms."],
    "seating": ["Comfortable for long sitting.", "Easy to move around.", "Sturdy everyday seat."],
    "storage": ["Keeps books and things tidy.", "Plenty of room for storage.", "Fits in a small flat."],
    "tables": ["A solid surface for work or meals.", "Easy to assemble.", "Wipes clean."],
    "sofas": ["Deep cushions.", "Fits a living room.", "Removable covers."],
    "beds": ["Quiet, solid frame.", "Slats included.", "Mattress not included."],
    "decor": ["Brightens up a room.", "Easy to place anywhere.", "Hand finished."],
}


def describe(kind, material, color, a):
    bits = [f"A {color} {material} {kind}."]
    if "holds_books" in a:
        bits.append(f"{a['shelves']} shelves, holds about {a['holds_books']} books.")
    if "height_cm" in a:
        bits.append(f"Height {a['height_cm']} cm.")
    if a.get("width_cm"):
        bits.append(f"Width {a['width_cm']} cm.")
    if a.get("seats"):
        bits.append(f"Seats {a['seats']}.")
    for k, txt in (("wheels", "On wheels."), ("adjustable", "Height adjustable."), ("armrests", "With armrests."),
                   ("dimmable", "Dimmable."), ("sleeper", "Folds out into a bed."), ("storage", "Storage drawers underneath.")):
        if a.get(k):
            bits.append(txt)
    if a.get("drawers"):
        bits.append(f"{a['drawers']} drawers.")
    if a.get("doors"):
        bits.append(f"{a['doors']} doors.")
    if "bulb" in a:
        bits.append(f"{a['bulb']} bulb.")
    if "size" in a:
        bits.append(f"{a['size'].capitalize()} size.")
    if "max_load_kg" in a:
        bits.append(f"Holds up to {a['max_load_kg']} kg.")
    return " ".join(bits)


def make_items(r):
    items = [
        {"id": "lamp", "name": "lamp", "category": "lighting", "kind": "lamp", "reserve": 100,
         "description": "The standard lamp. A white steel desk lamp, height 45 cm, LED bulb.",
         "attrs": {"material": "steel", "color": "white", "height_cm": 45}},
        {"id": "chair", "name": "chair", "category": "seating", "kind": "chair", "reserve": 180,
         "description": "The standard chair. A natural oak dining chair, height 90 cm.",
         "attrs": {"material": "oak", "color": "natural", "height_cm": 90}},
        {"id": "bookshelf", "name": "bookshelf", "category": "storage", "kind": "bookshelf", "reserve": 260,
         "description": "The standard bookshelf. A walnut bookshelf, 5 shelves, holds about 100 books. Height 180 cm.",
         "attrs": {"material": "walnut", "color": "brown", "height_cm": 180, "shelves": 5, "holds_books": 100}},
    ]
    seen = {i["name"] for i in items}
    kinds = list(KINDS)
    while len(items) < N_ITEMS:
        kind = r.choice(kinds)
        category, materials, (lo, hi), maker = KINDS[kind]
        material, color = r.choice(materials), r.choice(COLORS)
        name = f"{r.choice(MODELS)} {material} {kind}".title()
        if name in seen:
            name = f"{name} {r.choice(['II', 'Mini', 'Plus', 'Max', 'Classic', 'Studio', 'Pro', 'Lite'])}"
        if name in seen:
            continue
        seen.add(name)
        a = maker(r, kind)
        size_factor = 1 + (a.get("height_cm", 80) + a.get("width_cm", 80)) / 600
        reserve = int(r.uniform(lo, hi) * size_factor / 1.3) // 5 * 5 or 5
        items.append({"id": f"P-{len(items):05d}", "name": name, "category": category, "kind": kind,
                      "reserve": reserve, "description": describe(kind, material, color, a),
                      "attrs": {"material": material, "color": color, **a}})
    return items


SHOP_FOCUS = [  # (shop name, categories it carries)
    ("Lumen Lights", ["lighting"]), ("Bright Corner", ["lighting", "decor"]), ("Seat Society", ["seating"]),
    ("Office Nest", ["seating", "tables"]), ("Shelf Life", ["storage"]), ("Book Nook Furniture", ["storage"]),
    ("Table Talk", ["tables"]), ("Desk Works", ["tables", "seating"]), ("Sofa Street", ["sofas"]),
    ("Sleep Well Beds", ["beds"]), ("Rug Room", ["decor"]), ("Home Basics", ["lighting", "seating", "storage"]),
    ("Nordic Living", ["seating", "tables", "storage"]), ("Small Space Co", ["storage", "tables"]),
    ("Loft Supply", ["lighting", "tables"]), ("Cozy Den", ["sofas", "decor", "lighting"]),
    ("Study Hall", ["storage", "tables", "lighting"]), ("Patio & More", ["seating", "decor"]),
    ("Big Box Furniture", ["lighting", "seating", "storage", "tables", "sofas", "beds", "decor"]),
    ("Thrift Finds", ["seating", "storage", "lighting"]),
]


def make_shops(r, items):
    generated = [i for i in items if i["id"].startswith("P-")]
    shops = []
    for n, (name, cats) in enumerate(SHOP_FOCUS, start=1):
        factor = round(r.uniform(0.8, 1.35), 2)          # this shop's price level
        pool = [i for i in generated if i["category"] in cats]
        stock = r.sample(pool, min(len(pool), r.randint(120, 400)))
        shop_items = [{**i, "reserve": max(5, int(i["reserve"] * factor) // 5 * 5)} for i in stock]
        shops.append({"name": name, "categories": cats, "price_factor": factor,
                      "items": sorted(shop_items, key=lambda i: i["id"])})
    return shops


def main():
    r = random.Random(SEED)
    items = make_items(r)
    (HERE / "catalog.json").write_text(json.dumps({"name": "Harness Furniture", "items": items}, indent=1) + "\n")
    shops_dir = HERE / "shops"
    shops_dir.mkdir(exist_ok=True)
    for n, shop in enumerate(make_shops(r, items), start=1):
        (shops_dir / f"shop-{n:02d}.json").write_text(json.dumps(shop, indent=1) + "\n")
    print(f"catalog.json: {len(items)} items; shops/: {N_SHOPS} catalogs")


if __name__ == "__main__":
    main()
