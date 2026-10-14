"""Generate the catalogs the seller sells from.

    python gen_catalog.py      # writes catalogs/basic.json and catalogs/large.json

basic.json  the three items of the trade test (lamp 100, chair 180, bookshelf 260).
large.json  about 3000 generated furniture items for challenge 1. Each item has an
            id, a name, a category, numeric attributes, a description that repeats
            the attributes in words, and a reserve (the lowest price the seller may take).
Seeded, so the same file comes out every time.
"""
import json
import random
import re
from pathlib import Path

WOOD = ["oak", "pine", "walnut", "birch", "beech", "teak", "bamboo"]
METAL = ["steel", "aluminum", "brass"]
OTHER = ["rattan", "glass", "MDF laminate", "plastic"]
FABRIC = ["linen", "velvet", "leather", "wool", "cotton"]
COLORS = ["natural", "white", "black", "grey", "dark brown", "honey", "green", "navy", "beige"]
STYLES = ["Scandinavian", "industrial", "mid-century", "minimalist", "rustic", "modern", "classic", "Japanese"]
BRANDS = ["Nordal", "Hagen", "Orrin", "Kesta", "Lumo", "Brisa", "Tavik", "Solen", "Vardo", "Mirel", "Ostra", "Pella"]
MAT_FACTOR = {"oak": 1.35, "pine": 0.9, "walnut": 1.6, "birch": 1.1, "beech": 1.15, "teak": 1.7, "bamboo": 1.0,
              "steel": 1.0, "aluminum": 1.1, "brass": 1.5, "rattan": 1.05, "glass": 1.2, "MDF laminate": 0.7,
              "plastic": 0.55, "linen": 1.0, "velvet": 1.25, "leather": 1.7, "wool": 1.2, "cotton": 0.9}


def r(rng, lo, hi, step=1):
    return rng.randrange(lo, hi + 1, step)


# category -> (base price, materials, attribute maker, sentence maker)
def bookshelf(rng):
    shelves, width = r(rng, 2, 7), r(rng, 40, 140, 10)
    height = shelves * 34 + r(rng, 6, 20)
    return {"shelves": shelves, "width_cm": width, "height_cm": height, "books": shelves * width // 3}, \
        f"{shelves} shelves, holds about {shelves * width // 3} books, {height} cm tall, {width} cm wide", 0.6 + shelves * 0.12 + width / 300


def wall_shelf(rng):
    width, load = r(rng, 40, 160, 10), r(rng, 10, 40, 5)
    return {"width_cm": width, "height_cm": r(rng, 15, 30), "books": width // 3, "max_load_kg": load}, \
        f"wall mounted, {width} cm wide, holds about {width // 3} books, carries up to {load} kg", 0.5 + width / 200


def desk(rng):
    w, h = r(rng, 80, 180, 10), r(rng, 72, 76)
    return {"width_cm": w, "depth_cm": r(rng, 50, 80, 5), "height_cm": h}, f"{w} cm wide work surface, {h} cm tall", 0.7 + w / 200


def standing_desk(rng):
    w = r(rng, 100, 180, 10)
    lo, hi = r(rng, 62, 72), r(rng, 115, 128)
    return {"width_cm": w, "height_cm": lo, "max_height_cm": hi}, \
        f"electric height adjustable from {lo} to {hi} cm, {w} cm wide", 1.0 + w / 200


def office_chair(rng):
    seat, kg = r(rng, 42, 54), r(rng, 100, 150, 10)
    return {"seat_height_cm": seat, "height_cm": r(rng, 95, 130), "max_user_kg": kg}, \
        f"swivel office chair with lumbar support, seat height {seat} cm, for users up to {kg} kg", 1.0


def dining_chair(rng):
    seat = r(rng, 44, 48)
    return {"seat_height_cm": seat, "height_cm": r(rng, 78, 95)}, f"dining chair, seat height {seat} cm, stackable" if rng.random() < .4 else f"dining chair, seat height {seat} cm", 0.8


def stool(rng):
    h = rng.choice([45, 46, 65, 66, 75, 76])
    return {"height_cm": h}, f"{'bar' if h > 60 else 'low'} stool, {h} cm tall", 0.5


def armchair(rng):
    return {"width_cm": r(rng, 70, 95), "height_cm": r(rng, 80, 105)}, "upholstered armchair with wide armrests", 1.4


def sofa(rng):
    seats = r(rng, 2, 4)
    w = seats * 70 + r(rng, 20, 50)
    return {"seats": seats, "width_cm": w, "height_cm": r(rng, 75, 90)}, f"{seats}-seat sofa, {w} cm wide", 0.8 + seats * 0.6


def desk_lamp(rng):
    h, lm = r(rng, 30, 60), r(rng, 300, 1000, 50)
    return {"height_cm": h, "lumens": lm}, f"LED desk lamp, {lm} lumen, {h} cm tall, adjustable arm", 0.4 + lm / 2000


def floor_lamp(rng):
    h, lm = r(rng, 140, 185), r(rng, 800, 3000, 100)
    return {"height_cm": h, "lumens": lm}, f"floor lamp, {lm} lumen, {h} cm tall", 0.6 + lm / 4000


def pendant_light(rng):
    d = r(rng, 20, 60, 5)
    return {"diameter_cm": d}, f"ceiling pendant light, shade {d} cm across", 0.4 + d / 80


def coffee_table(rng):
    w = r(rng, 70, 130, 10)
    return {"width_cm": w, "height_cm": r(rng, 35, 48)}, f"coffee table, {w} cm long", 0.6 + w / 250


def dining_table(rng):
    seats = rng.choice([2, 4, 6, 8])
    return {"seats": seats, "width_cm": 50 + seats * 25, "height_cm": r(rng, 73, 77)}, \
        f"dining table for {seats} people, {50 + seats * 25} cm long", 0.6 + seats * 0.25


def side_table(rng):
    h = r(rng, 40, 65)
    return {"height_cm": h}, f"small side table, {h} cm tall", 0.4


def nightstand(rng):
    d = r(rng, 1, 3)
    return {"drawers": d, "height_cm": r(rng, 45, 65)}, f"bedside table with {d} drawer{'s' if d > 1 else ''}", 0.4 + d * 0.15


def dresser(rng):
    d = r(rng, 3, 8)
    return {"drawers": d, "width_cm": r(rng, 60, 160, 10), "height_cm": r(rng, 70, 130)}, f"chest of {d} drawers for clothes", 0.6 + d * 0.15


def wardrobe(rng):
    doors = r(rng, 2, 4)
    return {"doors": doors, "height_cm": r(rng, 180, 236), "width_cm": doors * 50}, \
        f"{doors}-door wardrobe with hanging rail, {doors * 50} cm wide", 1.2 + doors * 0.4


def tv_stand(rng):
    tv = rng.choice([43, 50, 55, 65, 75])
    return {"max_tv_inch": tv, "width_cm": tv * 2 + 20, "height_cm": r(rng, 40, 60)}, \
        f"TV stand for screens up to {tv} inch, {tv * 2 + 20} cm wide", 0.5 + tv / 100


def shoe_rack(rng):
    pairs = r(rng, 6, 30, 2)
    return {"pairs": pairs, "height_cm": r(rng, 40, 120)}, f"shoe rack for {pairs} pairs of shoes", 0.3 + pairs / 40


def coat_rack(rng):
    hooks = r(rng, 4, 12)
    return {"hooks": hooks, "height_cm": r(rng, 160, 190)}, f"standing coat rack with {hooks} hooks", 0.4


def plant_stand(rng):
    tiers = r(rng, 1, 5)
    return {"tiers": tiers, "height_cm": tiers * 25 + 10}, f"plant stand with {tiers} tier{'s' if tiers > 1 else ''}", 0.3 + tiers * 0.08


def rug(rng):
    w, l = rng.choice([(80, 150), (120, 170), (160, 230), (200, 300), (240, 340)])
    return {"width_cm": w, "length_cm": l}, f"rug {w} x {l} cm", 0.3 + w * l / 40000


def mirror(rng):
    h = r(rng, 40, 180, 10)
    return {"height_cm": h}, f"{'full length' if h > 140 else 'wall'} mirror, {h} cm tall", 0.4 + h / 200


def bed_frame(rng):
    size = rng.choice(["single", "double", "queen", "king"])
    return {"size": size, "width_cm": {"single": 90, "double": 140, "queen": 160, "king": 180}[size]}, \
        f"{size} bed frame with slatted base", {"single": 1.0, "double": 1.3, "queen": 1.5, "king": 1.8}[size]


def filing_cabinet(rng):
    d = r(rng, 2, 4)
    return {"drawers": d, "height_cm": d * 30 + 10}, f"lockable filing cabinet, {d} drawers for A4 folders", 0.6 + d * 0.15


def storage_cabinet(rng):
    s = r(rng, 2, 5)
    return {"shelves": s, "height_cm": s * 35 + 20, "doors": 2}, f"storage cabinet with doors and {s} shelves inside", 0.7 + s * 0.12


WOODISH = WOOD + ["MDF laminate"]
CATS = {
    "bookshelf": (180, WOODISH + METAL, bookshelf),
    "wall shelf": (60, WOODISH + METAL, wall_shelf),
    "desk": (200, WOODISH + METAL + ["glass"], desk),
    "standing desk": (380, WOODISH + METAL, standing_desk),
    "office chair": (170, FABRIC + ["plastic"], office_chair),
    "dining chair": (90, WOOD + METAL + ["rattan", "plastic"], dining_chair),
    "stool": (60, WOOD + METAL + ["plastic"], stool),
    "armchair": (260, FABRIC + ["rattan"], armchair),
    "sofa": (450, FABRIC, sofa),
    "desk lamp": (70, METAL + ["plastic", "bamboo"], desk_lamp),
    "floor lamp": (110, METAL + WOOD[:3] + ["rattan"], floor_lamp),
    "pendant light": (80, METAL + ["glass", "rattan", "bamboo"], pendant_light),
    "coffee table": (150, WOOD + METAL + ["glass"], coffee_table),
    "dining table": (300, WOOD + ["glass", "MDF laminate"], dining_table),
    "side table": (70, WOOD + METAL + ["glass", "rattan"], side_table),
    "nightstand": (90, WOODISH, nightstand),
    "dresser": (240, WOODISH, dresser),
    "wardrobe": (400, WOODISH, wardrobe),
    "tv stand": (160, WOODISH + METAL + ["glass"], tv_stand),
    "shoe rack": (50, WOOD + METAL + ["bamboo"], shoe_rack),
    "coat rack": (60, WOOD + METAL, coat_rack),
    "plant stand": (45, WOOD + METAL + ["bamboo", "rattan"], plant_stand),
    "rug": (90, ["wool", "cotton", "jute", "polyester"], rug),
    "mirror": (90, WOOD + METAL, mirror),
    "bed frame": (320, WOODISH + METAL + ["velvet", "linen"], bed_frame),
    "filing cabinet": (150, METAL + WOODISH, filing_cabinet),
    "storage cabinet": (200, WOODISH + METAL, storage_cabinet),
}
PER_CATEGORY = 112          # 27 categories x 112 = 3024 items
CODES = {c: "".join(w[0] for w in c.split()).upper() + ("X" if len(c.split()) == 1 else "") for c in CATS}


def cap(s):
    return s[0].upper() + s[1:]


def material_words(m):
    if m in WOOD:
        return f"solid {m} wood, wooden"
    if m == "MDF laminate":
        return "MDF with wood-look laminate"
    if m in METAL:
        return f"{m} frame, metal"
    return m


def large(seed=6):
    rng = random.Random(seed)
    items = []
    for cat, (base, mats, make) in CATS.items():
        for i in range(PER_CATEGORY):
            mat, color, style = rng.choice(mats), rng.choice(COLORS), rng.choice(STYLES)
            attrs, sentence, size = make(rng)
            stray = [n for n in re.findall(r"(?<![A-Za-z])\d+", sentence) if int(n) not in attrs.values()]
            assert not stray, f"{cat}: description numbers {stray} are not attributes: {sentence} {attrs}"
            attrs = {"material": mat, "wooden": mat in WOOD, "color": color, **attrs}
            reserve = max(15, round(base * MAT_FACTOR.get(mat, 1.0) * size * rng.uniform(0.85, 1.15)))
            iid = f"{CODES[cat]}-{i + 1:03d}"
            items.append({
                "id": iid,
                "name": f"{rng.choice(BRANDS)} {cap(mat.split()[0])} {cat.title()} {iid}",
                "category": cat,
                "description": f"{cap(style)} {cat} in {color}. {cap(sentence)}. {cap(material_words(mat))}.",
                "attrs": attrs,
                "reserve": reserve,
            })
    return items


BASIC = [
    {"id": "lamp", "name": "lamp", "category": "desk lamp", "description": "Desk lamp.", "attrs": {}, "reserve": 100},
    {"id": "chair", "name": "chair", "category": "office chair", "description": "Chair.", "attrs": {}, "reserve": 180},
    {"id": "bookshelf", "name": "bookshelf", "category": "bookshelf", "description": "Bookshelf.", "attrs": {}, "reserve": 260},
]

if __name__ == "__main__":
    out = Path(__file__).parent / "catalogs"
    out.mkdir(exist_ok=True)
    (out / "basic.json").write_text(json.dumps(BASIC, indent=1) + "\n", encoding="utf-8")
    items = large()
    (out / "large.json").write_text("[\n" + ",\n".join(json.dumps(i) for i in items) + "\n]\n", encoding="utf-8")
    print(f"basic.json: {len(BASIC)} items, large.json: {len(items)} items in {len(CATS)} categories")
