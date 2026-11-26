"""Cheap retrieval before the catalog candidates are handed to the harness."""

from __future__ import annotations

import json
import re
from pathlib import Path


SYNONYMS = {
    "books": {"book", "books", "bookshelf", "shelf", "library"},
    "wooden": {"wood", "wooden", "oak", "pine", "walnut"},
    "light": {"light", "lamp", "lighting", "desk"},
    "seat": {"seat", "chair", "sitting", "stool"},
}


def tokens(text: str) -> set[str]:
    raw = set(re.findall(r"[a-z0-9]+", text.lower()))
    expanded = set(raw)
    for word in raw:
        for group in SYNONYMS.values():
            if word in group:
                expanded.update(group)
    return expanded


def load_catalog(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def search(catalog: list[dict], query: str, limit: int = 6) -> list[dict]:
    query_tokens = tokens(query)
    query_numbers = set(re.findall(r"\d+", query))
    scored = []
    for product in catalog:
        text = f"{product['name']} {product['description']}"
        product_tokens = tokens(text)
        overlap = len(query_tokens & product_tokens)
        number_bonus = 3 * len(query_numbers & set(re.findall(r"\d+", text)))
        phrase_bonus = 4 if "books" in query_tokens and "bookshelf" in product_tokens else 0
        scored.append((overlap + number_bonus + phrase_bonus, product["id"], product))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [product for _, _, product in scored[:limit]]
