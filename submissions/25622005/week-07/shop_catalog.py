"""LAB 1, step 1: read the kit's shop list without choosing or trading.

Usage: python shop_catalog.py --shops .market/shops.json
Reviews keep the kit's newest-first order. Shop names identify remembered shops;
URLs are only their current contact addresses.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def load_shops(path: str | Path) -> list[dict]:
    shops = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(shops, list):
        raise ValueError("shop list must be a JSON array")
    for index, shop in enumerate(shops):
        label = f"shop[{index}]"
        if not isinstance(shop, dict):
            raise ValueError(f"{label} must be an object")
        if not isinstance(shop.get("name"), str) or not shop["name"].strip():
            raise ValueError(f"{label} needs a nonempty name")
        url = shop.get("url")
        if not isinstance(url, str):
            raise ValueError(f"{label} needs a URL")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{label} needs an HTTP(S) URL")
        reviews = shop.get("reviews")
        if not isinstance(reviews, list):
            raise ValueError(f"{label}.reviews must be an array")
        for review in reviews:
            if not isinstance(review, dict):
                raise ValueError(f"{label} contains a non-object review")
            stars = review.get("stars")
            if type(stars) is not int or not 1 <= stars <= 5:
                raise ValueError(f"{label} review stars must be an integer from 1 to 5")
            if not isinstance(review.get("review"), str):
                raise ValueError(f"{label} review text must be a string")
            if type(review.get("round")) is not int:
                raise ValueError(f"{label} review round must be an integer")
    return shops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", required=True, type=Path)
    args = parser.parse_args()
    try:
        shops = load_shops(args.shops)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"[catalog] {len(shops)} shops; inspection only, no trade")
    for shop in shops:
        print(json.dumps(shop, ensure_ascii=False))


if __name__ == "__main__":
    main()
