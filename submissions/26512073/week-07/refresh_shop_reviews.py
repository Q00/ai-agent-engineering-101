"""Save a fresh public review snapshot without replacing older evidence."""
from datetime import datetime, timezone
import json

import httpx

from pricing_state import DATA_DIR


def refresh_shop_reviews(shop_name="Afford Shop"):
    response = httpx.get(
        "https://market.ourolabs.ai/api/shop-reviews",
        params={"shop": shop_name},
        timeout=10,
    )
    response.raise_for_status()
    reviews = response.json()

    required = {"author", "stars", "review", "ts"}
    if not isinstance(reviews, list) or any(
        not isinstance(review, dict) or not required.issubset(review)
        for review in reviews
    ):
        raise ValueError("Unexpected review response format")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    folder = DATA_DIR / "logs" / "challenge-1" / "reviews"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"reviews-{stamp}.json"

    snapshot = {
        "captured_at": stamp,
        "shop": shop_name,
        "source": str(response.url),
        "status": response.status_code,
        "data": reviews,
    }
    with path.open("x", encoding="utf-8") as output:
        json.dump(snapshot, output, indent=2, ensure_ascii=False)

    return path


if __name__ == "__main__":
    print("Saved review snapshot:", refresh_shop_reviews())