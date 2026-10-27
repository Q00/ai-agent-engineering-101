"""Generate the 20 shops for challenge 2 from catalog.json (deterministic, seed 2006).

    python make_shops.py        # -> shops/shop-01.json .. shop-20.json

Each shop takes 1-4 categories of the big catalog, 30-60% of the items in each,
and multiplies every reserve by a shop price factor (0.80 budget .. 1.35 premium).
Its card name, description and skill say what it sells and how pricey it is,
so a buyer can choose shops from the cards alone. The same seller.py serves
every shop with --catalog shops/shop-NN.json.
"""
import json
import random
from pathlib import Path

SEED = 2006
SHOPS = [  # name, categories, price factor, pitch
    ("Lumen & Co", ["desk lamp", "floor lamp"], 1.05, "Lamps of every kind, from reading lights to floor lamps."),
    ("Bright Corner", ["desk lamp"], 0.85, "Cheap desk lamps for students and home offices."),
    ("Lantern House", ["floor lamp", "desk lamp", "rug"], 1.30, "Designer lighting and rugs, premium quality."),
    ("Shelf Life", ["bookshelf"], 0.90, "Bookshelves and bookcases at fair prices."),
    ("The Reading Room", ["bookshelf", "desk", "desk lamp"], 1.20, "Everything for a home library: shelves, desks, reading lamps."),
    ("Timber & Grain", ["bookshelf", "dresser", "wardrobe", "coffee table"], 1.15, "Solid wood furniture, handmade feel."),
    ("Budget Basics", ["bookshelf", "office chair", "rug", "desk lamp"], 0.80, "Low-cost basics for a first apartment."),
    ("SitWell", ["office chair", "dining chair"], 1.00, "Chairs for work and dining."),
    ("Ergo Works", ["office chair", "desk"], 1.25, "Ergonomic office chairs and standing desks."),
    ("Office Depot Plus", ["office chair", "desk", "bookshelf"], 0.95, "Office furniture: chairs, desks and shelving."),
    ("Rug Republic", ["rug"], 0.90, "Rugs and carpets, washable and outdoor options."),
    ("Soft Floors", ["rug", "sofa"], 1.10, "Rugs and sofas for a cozy living room."),
    ("Loom & Thread", ["rug", "armchair"], 1.30, "Hand-woven rugs and upholstered armchairs, premium."),
    ("Sofa Central", ["sofa", "armchair"], 1.00, "Sofas, sleeper sofas and armchairs."),
    ("Dining Hall", ["dining table", "dining chair", "bar stool"], 1.05, "Dining tables, chairs and kitchen stools."),
    ("Counter Culture", ["bar stool", "dining chair"], 0.90, "Bar and counter stools, dining chairs."),
    ("Sleep Well", ["bed frame", "wardrobe", "dresser"], 1.10, "Bedroom furniture: beds, wardrobes, dressers."),
    ("Media Nest", ["tv stand", "coffee table", "rug"], 1.00, "Living room media furniture and rugs."),
    ("Outlet Warehouse", ["desk lamp", "bookshelf", "office chair", "rug"], 0.85, "Discount outlet: mixed furniture, limited stock."),
    ("Maison Prestige", ["sofa", "dining table", "bookshelf", "floor lamp"], 1.35, "Luxury furniture for the discerning home."),
]


def main():
    random.seed(SEED)
    items = json.load(open("catalog.json", encoding="utf-8"))["items"]
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    Path("shops").mkdir(exist_ok=True)
    for n, (name, cats, factor, pitch) in enumerate(SHOPS, 1):
        stock = []
        for c in cats:
            pool = by_cat[c]
            for it in random.sample(pool, k=int(len(pool) * random.uniform(0.3, 0.6))):
                stock.append({**it, "reserve": max(10, round(it["reserve"] * factor))})
        tier = "budget prices" if factor < 0.95 else "premium prices" if factor > 1.15 else "mid-range prices"
        shop = {"name": name,
                "description": f"{pitch} Sells {', '.join(cats)} at {tier}. {len(stock)} items.",
                "skill_description": f"Sells {', '.join(cats)}. Describe what you need, then "
                                     "send a price; the shop answers with a counteroffer or a receipt.",
                "tags": cats, "price_factor": factor, "items": stock}
        json.dump(shop, open(f"shops/shop-{n:02d}.json", "w", encoding="utf-8"), indent=0)
        print(f"shop-{n:02d} {name:18} x{factor:.2f} {len(stock):4} items: {', '.join(cats)}")


if __name__ == "__main__":
    main()
