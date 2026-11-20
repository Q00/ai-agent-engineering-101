"""LAB 1 Step 1: read the shop-list file provided by the market kit."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def load_shops(path):
    """Keep shop names, URLs and reviews in their original order."""
    shops = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(shops, list):
        raise ValueError("Shop list must be a JSON array")
    for index, shop in enumerate(shops):
        if not isinstance(shop, dict):
            raise ValueError(f"Shop {index} must be an object")
        for field in ("name", "url"):
            if not isinstance(shop.get(field), str) or not shop[field].strip():
                raise ValueError(f"Shop {index} needs a nonempty {field}")
        url = urlsplit(shop["url"])
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError(f"Shop {index} needs an absolute HTTP(S) URL")
        if not isinstance(shop.get("reviews"), list):
            raise ValueError(f"Shop {index} needs a reviews array")
        for review in shop["reviews"]:
            if not isinstance(review, dict) or not {"stars", "review", "round"} <= review.keys():
                raise ValueError(f"Shop {index} has an invalid review object")
    return shops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", type=Path, required=True)
    args = parser.parse_args()
    try:
        shops = load_shops(args.shops)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot read shop list: {error}\n")
    print(f"Loaded {len(shops)} shops; review order preserved (newest first).")
    print(json.dumps(shops, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
