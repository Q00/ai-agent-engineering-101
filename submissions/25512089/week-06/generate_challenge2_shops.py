from __future__ import annotations

import json
from pathlib import Path


OUTPUT = Path("challenge2_shops.json")

ITEMS = [
    "lamp",
    "chair",
    "bookshelf",
    "desk",
    "side table",
]

BASE_RESERVE = {
    "lamp": 70,
    "chair": 120,
    "bookshelf": 180,
    "desk": 160,
    "side table": 80,
}

PRICE_SPREAD = {
    "lamp": 20,
    "chair": 30,
    "bookshelf": 40,
    "desk": 35,
    "side table": 25,
}


def main() -> None:
    shops: list[dict] = []

    for i in range(1, 21):
        base_index = (i - 1) % len(ITEMS)

        sold_items = [
            ITEMS[base_index],
            ITEMS[(base_index + 1) % len(ITEMS)],
            ITEMS[(base_index + 3) % len(ITEMS)],
        ]

        inventory: dict[str, dict] = {}

        for j, item in enumerate(sold_items):
            reserve = (
                BASE_RESERVE[item]
                + (
                    (i * 7 + j * 5)
                    % PRICE_SPREAD[item]
                )
            )

            advertised_price = (
                reserve
                + 20
                + ((i + j) % 3) * 5
            )

            inventory[item] = {
                "reserve": int(reserve),
                "advertised_price": int(
                    advertised_price
                ),
            }

        shop = {
            "shop_id": f"shop-{i:02d}",
            "name": f"Arun Shop {i:02d}",
            "port": 9300 + i,
            "url": (
                f"http://127.0.0.1:"
                f"{9300 + i}"
            ),
            "inventory": inventory,
        }

        shops.append(shop)

    OUTPUT.write_text(
        json.dumps(
            shops,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Wrote {len(shops)} shops "
        f"to {OUTPUT}"
    )


if __name__ == "__main__":
    main()