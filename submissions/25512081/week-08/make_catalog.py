"""Generate the large catalog for challenge 1 (deterministic, seed 606).

    python make_catalog.py            # -> catalog.json (3,200 items)

16 categories x 200 items. Each item has a name, a one-sentence description,
a reserve, and structured attributes (category, material, color, style and
numeric fields such as height_cm or capacity_books) that the seller's search
can filter on. Reserve = category base x material factor x size factor x
noise (+-12%). Names carry no digits, so the ask stays the last number in a
seller message.
"""
import json
import random

SEED = 606
PER_CATEGORY = 200
LINES = ("Halden Kiruna Lofoten Aster Birke Norra Tavi Oslo Brisa Ventor Calder Ember Juniper "
         "Linden Mara Nordic Orla Pike Quill Rowan Sable Tamsin Ulla Vela Wren Yara Zinne Alder "
         "Brook Cove Dune Fjord Glen Heath Isle Knoll Lark Moor Ness Opal").split()
STYLES = ["Nordic", "Industrial", "Classic", "Modern", "Rustic", "Minimal", "Mid-century",
          "Coastal", "Scandinavian", "Farmhouse"]
COLORS = ["black", "white", "grey", "natural", "brown", "green", "blue", "beige", "red", "yellow"]
WOODS = ["oak", "pine", "walnut", "teak", "birch", "bamboo", "beech"]
METALS = ["steel", "iron", "aluminum"]
MAT_FACTOR = {"oak": 1.25, "pine": 0.85, "walnut": 1.45, "teak": 1.5, "birch": 1.0, "bamboo": 0.9,
              "beech": 1.1, "steel": 1.0, "iron": 0.95, "aluminum": 1.05, "glass": 1.15,
              "plastic": 0.6, "rattan": 0.95, "fabric": 1.0, "leather": 1.6, "velvet": 1.3,
              "wool": 1.3, "cotton": 0.8, "jute": 0.75, "marble": 1.7}


def r(lo, hi, step=1):
    return random.randrange(lo, hi + 1, step)


# category: (base reserve, materials, attribute generator, description template)
def bookshelf():
    h = r(60, 210, 2); shelves = max(2, h // 38); w = r(40, 160, 5)
    return {"height_cm": h, "width_cm": w, "shelves": shelves,
            "capacity_books": shelves * w // 4}, \
        "{h} cm tall and {w} cm wide, with {shelves} shelves that hold about {cap} books"


CATS = {
    "bookshelf": (160, WOODS + METALS + ["plastic"], bookshelf),
    "desk": (180, WOODS + METALS + ["glass"], lambda: (
        lambda st, w: ({"height_cm": r(70, 120) if st else r(70, 76), "width_cm": w,
                        "standing": st, "drawers": r(0, 4)},
                       ("an adjustable standing desk" if st else "a desk")
                       + " {h} cm high and {w} cm wide with {drawers} drawers"))(
        random.random() < 0.25, r(80, 180, 10))),
    "office chair": (140, ["fabric", "leather", "steel", "plastic", "velvet"], lambda: (
        {"seat_height_cm": r(42, 55), "armrests": random.random() < 0.7,
         "adjustable": random.random() < 0.8},
        "seat height {seat} cm, {arm}, {adj}")),
    "dining chair": (70, WOODS + METALS + ["rattan", "plastic", "velvet"], lambda: (
        {"seat_height_cm": r(44, 48), "stackable": random.random() < 0.3},
        "seat height {seat} cm{stack}")),
    "bar stool": (60, WOODS + METALS + ["plastic", "rattan"], lambda: (
        lambda sh: ({"seat_height_cm": sh, "counter_height": sh <= 68, "backrest": random.random() < 0.5},
                    "seat height {seat} cm, made for a " + ("kitchen counter" if sh <= 68 else "bar")
                    + "{back}"))(r(60, 80))),
    "sofa": (420, ["fabric", "leather", "velvet", "cotton"], lambda: (
        lambda s: ({"seats": s, "width_cm": 70 + s * 60 + r(0, 30), "sleeper": random.random() < 0.2},
                   "seats {seats}, {w} cm wide{sleep}"))(r(2, 4))),
    "armchair": (210, ["fabric", "leather", "velvet", "rattan", "wool"], lambda: (
        {"seats": 1, "recliner": random.random() < 0.3}, "a single seat{recl}")),
    "coffee table": (110, WOODS + ["glass", "marble", "steel"], lambda: (
        {"height_cm": r(35, 50), "width_cm": r(60, 130, 5), "storage": random.random() < 0.4},
        "{h} cm high and {w} cm wide{stor}")),
    "dining table": (300, WOODS + ["glass", "marble"], lambda: (
        lambda s: ({"seats": s, "width_cm": 60 + s * 22, "extendable": random.random() < 0.3},
                   "seats {seats}, {w} cm long{ext}"))(random.choice([2, 4, 4, 6, 6, 8, 10]))),
    "bed frame": (330, WOODS + METALS + ["fabric"], lambda: (
        lambda sz: ({"size": sz, "storage": random.random() < 0.35},
                    "{size} size{stor}"))(random.choice(["single", "double", "queen", "king"]))),
    "wardrobe": (360, WOODS + ["steel"], lambda: (
        {"height_cm": r(150, 220, 5), "doors": r(1, 4), "mirror": random.random() < 0.3},
        "{h} cm tall with {doors} doors{mirr}")),
    "dresser": (240, WOODS, lambda: (
        {"drawers": r(3, 9), "height_cm": r(70, 130, 5)}, "{drawers} drawers, {h} cm tall")),
    "desk lamp": (45, METALS + ["plastic", "glass", "bamboo"], lambda: (
        {"height_cm": r(30, 65), "brightness_lm": r(250, 1200, 50), "dimmable": random.random() < 0.6},
        "{h} cm tall, {lm} lumens{dim}, for reading or a desk")),
    "floor lamp": (90, METALS + ["bamboo", "rattan", "glass"], lambda: (
        {"height_cm": r(130, 195), "brightness_lm": r(600, 2500, 100), "dimmable": random.random() < 0.6},
        "{h} cm tall, {lm} lumens{dim}")),
    "rug": (80, ["wool", "cotton", "jute", "plastic"], lambda: (
        lambda w: ({"width_cm": w, "length_cm": w + r(40, 120, 10), "washable": random.random() < 0.4,
                    "outdoor": random.random() < 0.2},
                   "{w} x {l} cm{wash}{out}"))(r(60, 240, 20))),
    "tv stand": (130, WOODS + ["glass", "steel"], lambda: (
        lambda w: ({"width_cm": w, "max_tv_in": w // 2 + 10, "storage": random.random() < 0.6},
                   "{w} cm wide, holds a TV up to {tv} inches{stor}"))(r(80, 200, 10))),
}


def describe(cat, style, material, color, attrs, tmpl):
    fill = {"h": attrs.get("height_cm"), "w": attrs.get("width_cm"),
            "shelves": attrs.get("shelves"), "cap": attrs.get("capacity_books"),
            "drawers": attrs.get("drawers"), "seat": attrs.get("seat_height_cm"),
            "seats": attrs.get("seats"), "doors": attrs.get("doors"), "size": attrs.get("size"),
            "lm": attrs.get("brightness_lm"), "l": attrs.get("length_cm"), "tv": attrs.get("max_tv_in"),
            "arm": "with armrests" if attrs.get("armrests") else "no armrests",
            "adj": "adjustable height" if attrs.get("adjustable") else "fixed height",
            "stack": ", stackable" if attrs.get("stackable") else "",
            "back": ", with a backrest" if attrs.get("backrest") else ", backless",
            "sleep": ", folds out into a bed" if attrs.get("sleeper") else "",
            "recl": ", reclines" if attrs.get("recliner") else "",
            "stor": ", with storage" if attrs.get("storage") else "",
            "ext": ", extendable" if attrs.get("extendable") else "",
            "mirr": ", with a mirror" if attrs.get("mirror") else "",
            "dim": ", dimmable" if attrs.get("dimmable") else "",
            "wash": ", machine washable" if attrs.get("washable") else "",
            "out": ", for indoor and outdoor use" if attrs.get("outdoor") else ""}
    body = tmpl.format(**fill)
    art = "An" if style[0] in "AEIOU" else "A"
    return f"{art} {style} {color} {material} {cat}: {body}."


def size_factor(attrs):
    f = 1.0
    for k, ref in (("height_cm", 100), ("width_cm", 120), ("seats", 3), ("capacity_books", 120)):
        if isinstance(attrs.get(k), (int, float)) and not isinstance(attrs.get(k), bool):
            f *= 0.8 + 0.4 * min(attrs[k] / ref, 2.0) / 2
    return f


def main():
    random.seed(SEED)
    items, seen = [], set()
    for cat, (base, mats, gen) in CATS.items():
        prefix = "".join(w[0] for w in cat.split()).upper() + cat.split()[-1][:2].upper()
        n = 0
        while n < PER_CATEGORY:
            material, style, color = random.choice(mats), random.choice(STYLES), random.choice(COLORS)
            line = random.choice(LINES)
            name = f"{line} {material} {cat}"
            if name in seen:
                name = f"{line} {style.lower()} {material} {cat}"
            if name in seen:
                continue
            seen.add(name)
            attrs, tmpl = gen()
            reserve = round(base * MAT_FACTOR[material] * size_factor(attrs) * random.uniform(0.88, 1.12))
            n += 1
            items.append({"id": f"{prefix}-{n:04d}", "name": name, "category": cat,
                          "material": material, "color": color, "style": style,
                          "description": describe(cat, style, material, color, attrs, tmpl),
                          "reserve": max(15, reserve), **attrs})
    out = {"name": "Opus Furniture Warehouse",
           "description": f"A furniture warehouse with {len(items)} items: "
                          + ", ".join(CATS) + ". Negotiates prices.",
           "items": items}
    json.dump(out, open("catalog.json", "w", encoding="utf-8"), indent=0)
    print(f"catalog.json: {len(items)} items, {len(CATS)} categories")


if __name__ == "__main__":
    main()
