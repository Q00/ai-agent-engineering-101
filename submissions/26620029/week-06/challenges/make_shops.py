"""Writes challenges/shops/S01.json ... S20.json: the challenge-2 directory of 20 shops.

Each shop is a copy of seller.py with its own name, a few categories, a seeded
subset of those categories' items, and its own price factor (0.8 to 1.4 x catalog).
The five shopping-list categories are spread so each is sold by 6 to 8 shops.
"""
import json
import random
from pathlib import Path

NAMES = ["Bright Corner", "Oak & Pine", "Sit Well", "Floor Stories", "Door & Hall", "Lumen Works",
         "Shelf Life", "Back Office", "Rug Republic", "Step Inside", "Glow Lab", "Timber Yard",
         "Ergo Depot", "Soft Ground", "Entryway Co", "Lamp Post", "Book Nook", "Chair Market",
         "Weave House", "Mudroom Supply"]
LIST_CATS = ["desk lamp", "bookshelf", "office chair", "rug", "shoe cabinet"]
OTHER = ["floor lamp", "dining chair", "stool", "desk", "coffee table", "side table", "sofa", "mirror",
         "wardrobe", "bed frame", "coat rack", "plant stand"]


def main():
    r = random.Random(20)
    out = Path(__file__).with_name("shops")
    out.mkdir(exist_ok=True)
    for i, name in enumerate(NAMES):
        cats = [LIST_CATS[i % 5]] + ([LIST_CATS[(i + 2) % 5]] if i % 3 else []) + r.sample(OTHER, r.randint(1, 3))
        cfg = {"id": f"S{i + 1:02d}", "name": name, "categories": cats, "factor": round(r.uniform(0.8, 1.4), 2),
               "keep": round(r.uniform(0.3, 0.6), 2), "seed": 100 + i,
               "blurb": f"{name}: a small furniture shop selling {', '.join(cats)}. Prices are negotiable."}
        (out / f"{cfg['id']}.json").write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")
        print(cfg["id"], name, cats, cfg["factor"])


if __name__ == "__main__":
    main()
