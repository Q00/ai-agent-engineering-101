"""Generates the challenge-2 directory: 20 shop configs for seller.py (seeded).

    uv run python make_shops.py              # seed 0 -> shops/shop-01.json ... shop-20.json

Every shop sells from the same catalog (catalog/catalog.json) but carries only
2-5 categories around one theme (lighting, seating, home office, ...) and sets
its floors at a price level (reserve x price_factor). The Agent Card says both
in words: the description names the specialty and the price positioning, the
skill lists the categories. Themes overlap, so most categories are sold by
several shops at different price levels and the buyer has a real choice.
"""

import argparse
import json
import pathlib
import random

THEMES = {
    "lighting": ["desk lamp", "floor lamp", "pendant lamp"],
    "seating": ["office chair", "dining chair", "armchair", "stool", "sofa"],
    "home office": ["desk", "office chair", "desk lamp", "bookshelf"],
    "living room": ["sofa", "armchair", "coffee table", "tv stand", "rug", "floor lamp"],
    "dining room": ["dining table", "dining chair", "pendant lamp", "cabinet"],
    "bedroom": ["bed frame", "nightstand", "wardrobe", "mirror"],
    "storage": ["bookshelf", "wardrobe", "cabinet", "shoe rack", "tv stand"],
    "tables": ["desk", "dining table", "coffee table", "nightstand"],
    "hallway": ["shoe rack", "mirror", "cabinet", "stool"],
    "soft furnishings": ["rug", "armchair", "sofa", "mirror"],
}
# price_factor -> (tag, how the card describes the prices)
LEVELS = {
    0.8: ("discount", "Discount warehouse: some of the lowest prices in town, plain finishes, no frills."),
    0.9: ("value", "Value shop: solid everyday pieces at sensible prices."),
    1.0: ("mid-range", "Mid-range shop: fair market prices."),
    1.15: ("upscale", "Upscale showroom: better finishes, prices somewhat above the market."),
    1.35: ("luxury", "Luxury boutique: designer pieces, priced well above the market."),
}  # fmt: skip
FIRST = ["Maple", "Copper", "Juniper", "Linden", "Harbor", "Willow", "Granite", "Saffron", "Cedar",
         "Birchwood", "Larkspur", "Northgate", "Amber", "Riverside", "Foxglove", "Ironbark", "Hollow",
         "Bluebell", "Kestrel", "Sable"]  # fmt: skip
LAST = {
    "lighting": "Lighting", "seating": "Seating Co.", "home office": "Home Office", "living room": "Living",
    "dining room": "Dining", "bedroom": "Bedroom Studio", "storage": "Storage", "tables": "Table Works",
    "hallway": "Hallway & Entry", "soft furnishings": "Soft Furnishings",
}  # fmt: skip
SKILL = (
    "We sell {cats}, and nothing else. Describe the piece you need in your own words (use, size, "
    "material, capacity) and we offer a matching item from our range with a price. Then send your "
    "offer as a number; we answer with a counteroffer or close the sale with a receipt."
)


def plural(category: str) -> str:
    return category + ("es" if category.endswith(("ch", "sh")) else "s")


def make_shops(seed: int, n: int) -> list[dict]:
    """Draws n shops; redraws (same RNG) until every category is sold by at least two shops."""
    rng = random.Random(seed)
    while True:
        shops = draw(rng, n)
        counts = {c: sum(c in s["categories"] for s in shops) for t in THEMES.values() for c in t}
        if min(counts.values()) >= 2:
            return shops


def draw(rng: random.Random, n: int) -> list[dict]:
    themes = list(THEMES) * (n // len(THEMES) + 1)
    names = rng.sample(FIRST, n)
    shops = []
    for i in range(n):
        theme = themes[i]
        cats = rng.sample(THEMES[theme], rng.randint(2, min(5, len(THEMES[theme]))))
        factor = rng.choice(list(LEVELS))
        tag, positioning = LEVELS[factor]
        listed = ", ".join(plural(c) for c in cats)
        shops.append({
            "name": f"{names[i]} {LAST[theme]}",
            "description": f"{theme.capitalize()} specialist. {positioning} We carry {listed}.",
            "skill_description": SKILL.format(cats=listed),
            "tags": [theme, tag, *cats],
            "catalog": "catalog/catalog.json",
            "categories": cats,
            "price_factor": factor,
        })  # fmt: skip
    return shops


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out-dir", default="shops")
    args = ap.parse_args()
    out = pathlib.Path(args.out_dir)
    out.mkdir(exist_ok=True)
    shops = make_shops(args.seed, args.n)
    for i, shop in enumerate(shops, 1):
        (out / f"shop-{i:02d}.json").write_text(json.dumps(shop, indent=1) + "\n")
    carried = {}
    for shop in shops:
        for c in shop["categories"]:
            carried.setdefault(c, []).append(f"{shop['name']} x{shop['price_factor']}")
    for c in sorted(carried):
        print(f"{c:14s} {len(carried[c])} shops: {', '.join(carried[c])}")
    print(f"{len(shops)} shops -> {out}/; {len(carried)} categories carried")


if __name__ == "__main__":
    main()
