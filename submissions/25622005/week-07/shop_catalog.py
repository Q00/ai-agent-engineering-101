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


def choose_shop(shops: list[dict], memory: list[dict]) -> tuple[dict, str]:
    """User's policy: combine personal experience and other buyers' reviews.

    Memory is a list of trades with shop_name and stars (1..5), optionally
    item, price and outcome. URLs never identify past experience.
    Each source has equal weight; an absent source has neutral score 3.
    Ties use name for reproducibility, independent of the input list order.
    """
    if not shops:
        raise ValueError("no shops available")
    if not isinstance(memory, list):
        raise ValueError("memory must be a JSON array of trades")
    for trade in memory:
        if not isinstance(trade, dict) or not isinstance(trade.get("shop_name"), str):
            raise ValueError("memory trade needs shop_name")
        stars = trade.get("stars")
        if type(stars) is not int or not 1 <= stars <= 5:
            raise ValueError("memory trade stars must be an integer from 1 to 5")

    ranked = []
    for shop in shops:
        own = [t["stars"] for t in memory if t["shop_name"] == shop["name"]]
        reviews = [r["stars"] for r in shop["reviews"]]
        own_mean = sum(own) / len(own) if own else 3.0
        review_mean = sum(reviews) / len(reviews) if reviews else 3.0
        score = 0.5 * own_mean + 0.5 * review_mean
        reason = (f"{shop['name']}: score={score:.2f}; "
                  f"own={own_mean:.2f} ({len(own)} trades), "
                  f"reviews={review_mean:.2f} ({len(reviews)} reviews); "
                  "weights=50:50, missing source=3, ties by name")
        ranked.append((score, shop, reason))
    _, chosen, reason = sorted(ranked, key=lambda row: (-row[0], row[1]["name"]))[0]
    return chosen, reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", required=True, type=Path)
    parser.add_argument("--memory", type=Path, help="optional JSON array of past trades")
    parser.add_argument("--choose", action="store_true", help="show the score-based choice without trading")
    args = parser.parse_args()
    try:
        shops = load_shops(args.shops)
        memory = json.loads(args.memory.read_text(encoding="utf-8")) if args.memory else []
        selection = choose_shop(shops, memory) if args.choose else None
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"[catalog] {len(shops)} shops; inspection only, no trade")
    for shop in shops:
        print(json.dumps(shop, ensure_ascii=False))
    if selection:
        print(f"[choose] {selection[1]}")


if __name__ == "__main__":
    main()
