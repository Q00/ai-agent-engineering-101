"""Challenge 1: retrieve five products, then let the harness choose among them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Protocol

from catalog import CatalogIndex, Match, Product, catalog_prompt
from harness import CommandHarness, Decision


class CatalogHarness(Protocol):
    def decide(self, prompt: str, fallback: Decision) -> Decision:
        """Choose one retrieved product."""


def select_catalog_product(
    query: str,
    index: CatalogIndex,
    harness: CatalogHarness,
    limit: int = 5,
) -> tuple[Product, list[Match]]:
    """Return a harness-selected product, restricted to retrieved candidates."""
    candidates = index.search(query, limit=limit)
    if not candidates:
        raise ValueError("no catalog candidate matched the description")
    fallback = Decision("select", None, candidates[0].product.product_id)
    decision = harness.decide(catalog_prompt(query, candidates), fallback)
    selected = next(
        (
            match.product
            for match in candidates
            if match.product.product_id in decision.reason
        ),
        candidates[0].product,
    )
    return selected, candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--catalog", type=Path, default=Path("catalog.json"))
    parser.add_argument("--harness-cmd")
    args = parser.parse_args()
    index = CatalogIndex.load(args.catalog)
    selected, candidates = select_catalog_product(
        args.query,
        index,
        CommandHarness(args.harness_cmd),
    )
    print(
        json.dumps(
            {
                "event": "catalog_selection",
                "catalog_size": len(index.products),
                "query": args.query,
                "candidate_ids": [match.product.product_id for match in candidates],
                "selected": selected.product_id,
                "selected_name": selected.name,
                "reserve": selected.reserve,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()


