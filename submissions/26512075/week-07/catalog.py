"""Bounded, deterministic catalog retrieval before the model chooses an item."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = ROOT / "catalog" / "items.jsonl"
BASE = {
    "lamp": {"id": "lamp", "name": "lamp", "description": "desk lamp for reading", "reserve": 100},
    "chair": {"id": "chair", "name": "chair", "description": "desk chair for sitting", "reserve": 180},
    "bookshelf": {"id": "bookshelf", "name": "bookshelf", "description": "shelf to keep books", "reserve": 260},
}
WORDS = {
    "bookshelf": ("books", "bookcase", "bookshelf", "shelf", "shelves", "shelving"),
    "chair": ("chair", "seat", "sitting", "sit"),
    "lamp": ("lamp", "lighting", "light", "reading light"),
}


def load(path: Path = DEFAULT_CATALOG) -> list[dict]:
    items = list(BASE.values())
    with path.open(encoding="utf-8") as src:
        for line in src:
            item = json.loads(line)
            if not {"id", "name", "description", "reserve", "category"} <= item.keys():
                raise ValueError("Incomplete catalog entry")
            if not isinstance(item["reserve"], int) or item["reserve"] <= 0:
                raise ValueError(f"Invalid reserve for {item['id']}")
            items.append(item)
    if len({x["id"] for x in items}) != len(items):
        raise ValueError("Duplicate catalog ID")
    return items


def search(query: str, catalog: list[dict], limit: int = 8) -> list[dict]:
    """Hard constraints prevent a similar but unsuitable item being chosen."""
    q = query.lower()
    category = next((cat for cat, words in WORDS.items() if any(w in q for w in words)), None)
    material = "wood" if re.search(r"\bwood(?:en)?\b", q) else None
    if "metal" in q:
        material = "metal"
    count = re.search(r"(?:about|around|at least|hold|keep|for)\s+(\d+)\s+books?", q)
    min_capacity = int(count.group(1)) if count else None
    height = re.search(r"(?:under|below|no taller than|less than)\s+(\d+)\s*cm", q)
    max_height = int(height.group(1)) if height else (75 if "not taller than my desk" in q else None)
    terms = set(re.findall(r"[a-z]+", q)) - {"something", "about", "with", "than", "desk", "want", "buy"}
    scored = []
    for item in catalog:
        if category and item.get("category", item["id"]) != category:
            continue
        if material and item.get("material") != material:
            continue
        if min_capacity is not None and int(item.get("capacity_books", 0)) < min_capacity:
            continue
        if max_height is not None and int(item.get("height_cm", 10_000)) > max_height:
            continue
        words = set(re.findall(r"[a-z]+", item["name"].lower() + " " + item["description"].lower()))
        score = len(terms & words) * 10
        if category:
            score += 20
        if min_capacity is not None:
            score -= abs(int(item["capacity_books"]) - min_capacity) / 10
        if max_height is not None:
            score += int(item["height_cm"]) / 100
        scored.append((score, item["id"], item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [x[2] for x in scored[:limit]]
