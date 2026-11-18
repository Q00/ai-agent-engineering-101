from __future__ import annotations

import json

from challenge1_catalog import (
    load_catalog,
    search_catalog,
)
from challenge1_harness import (
    choose_product,
    description_to_criteria,
)


REQUEST = (
    "I need something wooden to keep about "
    "40 books, and it should not be taller "
    "than my desk."
)


def main() -> None:
    catalog = load_catalog()

    print(
        f"[CHALLENGE1] catalog_size={len(catalog)}"
    )

    print(
        f"[CHALLENGE1] buyer_request={REQUEST!r}"
    )

    criteria = description_to_criteria(
        REQUEST
    )

    print(
        "[CHALLENGE1] criteria="
        + json.dumps(
            criteria,
            ensure_ascii=False,
        )
    )

    candidates = search_catalog(
        catalog,
        criteria,
        limit=8,
    )

    print(
        f"[CHALLENGE1] candidates="
        f"{len(candidates)}"
    )

    for product in candidates:
        print(
            "[CANDIDATE] "
            f"{product['sku']} | "
            f"{product['name']} | "
            f"reserve={product['reserve']} | "
            f"height={product['height_cm']} | "
            f"capacity={product['capacity']}"
        )

    if not candidates:
        raise RuntimeError(
            "No matching candidates."
        )

    decision = choose_product(
        buyer_description=REQUEST,
        candidates=candidates,
    )

    product = decision["product"]

    print(
        "[CHALLENGE1 HARNESS] "
        + json.dumps(
            {
                "action": decision["action"],
                "sku": decision["sku"],
                "price": decision["price"],
            }
        )
    )

    print(
        "[CHALLENGE1 SELECTED] "
        f"{product['name']} | "
        f"reserve={product['reserve']} | "
        f"ask={decision['price']}"
    )

    assert len(catalog) == 4000
    assert len(candidates) <= 8

    assert product["category"] == "bookshelf"
    assert product["material"] == "wood"
    assert product["height_cm"] <= 85

    assert product["capacity"] is not None
    assert 35 <= product["capacity"] <= 45

    assert (
        decision["price"]
        >= product["reserve"]
    )

    print(
        "[CHALLENGE1] PASS"
    )


if __name__ == "__main__":
    main()