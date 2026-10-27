"""Narrow the large catalog before a harness chooses a product."""

from __future__ import annotations

import json
from pathlib import Path
import re

from harness import decide, trace


def normalize(text: str) -> str:
    text = text.lower()
    for phrase, replacement in (
        ("wooden", "wood"), ("bookcase", "bookshelf"),
        ("book storage", "bookshelf"), ("reading light", "reading lamp"),
        ("seat", "chair"),
    ):
        text = text.replace(phrase, replacement)
    return text


class Catalog:
    def __init__(self, path: str | Path) -> None:
        self.products = json.loads(Path(path).read_text(encoding="utf-8"))
        self.by_id = {product["id"]: product for product in self.products}

    def shortlist(self, request: str, limit: int = 12) -> list[dict]:
        query = normalize(request)
        words = set(re.findall(r"[a-z]+", query))
        category = next((name for name in ("bookshelf", "chair", "lamp") if name in query), None)
        if category is None and "books" in words:
            category = "bookshelf"
        material = next((name for name in ("wood", "bamboo", "metal", "plastic") if name in words), None)
        capacity_match = re.search(r"(?:about\s+)?(\d+)\s+books", query)
        capacity = int(capacity_match.group(1)) if capacity_match else None
        max_height_match = re.search(r"(?:under|below|shorter than|not taller than)\s+(\d+)\s*cm", query)
        max_height = int(max_height_match.group(1)) if max_height_match else None
        if max_height is None and "not taller than my desk" in query:
            max_height = 75

        scored = []
        for product in self.products:
            if category and product["category"] != category:
                continue
            if material and product.get("material") != material:
                continue
            if max_height is not None and product.get("height_cm", 0) > max_height:
                continue
            title_words = set(re.findall(r"[a-z]+", normalize(product["name"])))
            score = len(words & title_words) * 3
            if capacity is not None:
                score += 20 - min(20, abs(product.get("capacity_books", 0) - capacity))
            if "desk" in words and "desk" in product["description"].lower():
                score += 4
            scored.append((score, product))
        scored.sort(key=lambda entry: (-entry[0], entry[1]["reserve"], entry[1]["id"]))
        return [product for _, product in scored[:limit]]

    def choose(self, request: str) -> dict:
        candidates = self.shortlist(request)
        if not candidates:
            raise ValueError("no catalog candidates")
        compact = [
            {key: product[key] for key in ("id", "name", "description", "reserve")}
            for product in candidates
        ]
        trace("catalog_shortlist", query=request, total=len(self.products), candidates=compact)
        choice = decide("catalog selector", {
            "buyer_description": request,
            "candidates": compact,
            "objective": "Choose exactly one matching product. The action must be its id; price must be null.",
        }, [product["id"] for product in candidates])
        selected = self.by_id[choice["action"]]
        trace("catalog_selected", query=request, selected=selected["id"])
        return selected
