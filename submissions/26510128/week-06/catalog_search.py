"""Small retrieval layer for Challenge 1; only a shortlist reaches the model."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")
ALIASES = {
    "shelf": "bookshelf", "books": "bookshelf", "book": "bookshelf",
    "storage": "bookshelf", "sit": "chair", "seat": "chair",
    "reading": "lamp", "light": "lamp", "lighting": "lamp",
}


def load_catalog() -> list[dict[str, Any]]:
    if not CATALOG_PATH.exists():
        return []
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def shortlist(description: str, catalog: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """Score matching features without pasting the whole catalog into the LLM."""
    def terms(s: str) -> set[str]:
        words = re.findall(r"[a-z]+|\d+", s.lower())
        return {ALIASES.get(w, w) for w in words if len(w) > 2 or w.isdigit()}

    query_terms = terms(description)
    ranked = []
    for row in catalog:
        name_terms = terms(row["name"])
        desc_terms = terms(row["description"])
        score = 3 * len(query_terms & name_terms) + len(query_terms & desc_terms)
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return [row for _, row in ranked[:limit]]
