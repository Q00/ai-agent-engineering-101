"""Write the 20 shop configs for challenge 2 (shops/shop-01.json ... shop-20.json).

    python make_shops.py

Each shop sells a few categories of catalogs/large.json at its own price factor
(0.8 to 1.4 times the catalog reserve). The card description says whether the shop is
a discount or a premium shop, so the buyer has something to choose by. Seeded.
"""
import json
import random
from pathlib import Path

from gen_catalog import CATS

FIRST = ["Oak", "Linden", "North", "Harbor", "Maple", "Copper", "Willow", "Granite", "Cedar", "Ember"]
SECOND = ["House", "Living", "Works", "Depot", "Studio", "Outlet", "Corner", "Market", "Loft", "Yard"]


def main(seed=20, n=20):
    rng = random.Random(seed)
    out = Path(__file__).parent / "shops"
    out.mkdir(exist_ok=True)
    cats = list(CATS)
    names = rng.sample([f"{a} {b}" for a in FIRST for b in SECOND], n)
    for k in range(n):
        sells = sorted(rng.sample(cats, rng.randint(3, 7)))
        factor = round(rng.uniform(0.8, 1.4), 2)
        tier = "discount prices" if factor < 0.95 else "premium prices" if factor > 1.2 else "regular prices"
        cfg = {
            "name": names[k],
            "description": f"Furniture shop with {tier}. Sells {', '.join(sells)}. Negotiates the price.",
            "catalog": "catalogs/large.json",
            "categories": sells,
            "price_factor": factor,
            "port": 9501 + k,
        }
        (out / f"shop-{k + 1:02d}.json").write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")
        print(f"shop-{k + 1:02d} {names[k]:16s} x{factor} {sells}")


if __name__ == "__main__":
    main()
