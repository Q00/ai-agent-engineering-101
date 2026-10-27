"""Bounded catalog retrieval; a harness still chooses the item and every price.

Search uses a small, explicit synonym vocabulary and only a few documented
physical constraints. It is not a model or a negotiation policy. Unknown needs
remain visible in the original query for the harness to interpret. Product IDs,
descriptions, dimensions and reserves are loaded from the committed JSONL file.
"""
from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(__file__).with_name("catalog.jsonl")
MAX_CANDIDATES = 32

CHALLENGE_QUERIES = [
    {
        "query": "something wooden to keep about 40 books, not taller than my desk",
        "expected_id": "oak-low-40",
        "category": "bookshelf",
        "constraints": {"material": "wood", "capacity_books": 40, "max_height_cm": 75},
    },
    {
        "query": "a small warm reading light with USB-C charging, adjustable brightness, at most 35 cm tall",
        "expected_id": "amber-study-lamp",
        "category": "lamp",
        "constraints": {"usb_c": True, "dimmable": True, "max_height_cm": 35},
    },
    {
        "query": "a breathable mesh seat with lumbar support, a footrest and adjustable height",
        "expected_id": "mesh-back-support-chair",
        "category": "chair",
        "constraints": {"material": "mesh", "lumbar_support": True, "footrest": True},
    },
]

_SYNONYMS = {
    "wooden": "wood", "oak": "wood", "pine": "wood", "bamboo": "wood",
    "원목": "wood", "나무": "wood", "books": "book", "bookcases": "bookshelf",
    "bookcase": "bookshelf", "shelves": "bookshelf", "shelving": "bookshelf",
    "shelf": "bookshelf", "책장": "bookshelf", "책": "book", "lamps": "lamp",
    "lighting": "lamp", "light": "lamp", "lights": "lamp", "조명": "lamp",
    "chairs": "chair", "seat": "chair", "seating": "chair", "의자": "chair",
    "tables": "desk", "table": "desk", "책상": "desk", "organizer": "storage",
    "cabinet": "storage", "metallic": "metal", "steel": "metal", "aluminium": "metal",
    "aluminum": "metal", "밝기": "dimmable", "brightness": "dimmable",
    "breathable": "mesh", "ergonomic": "lumbar", "charged": "charging",
}
_STOP = set("a an the i want to buy something for with and or my of it please at in on is not than".split())


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+|[가-힣]+", text.lower().replace("usb-c", "usbc"))
    return [_SYNONYMS.get(word, word) for word in words if word not in _STOP]


def _constraints(query: str) -> dict[str, Any]:
    """Extract explicit physical constraints, never monetary limits or prices."""
    lower = query.lower()
    tokens = set(_tokens(query))
    constraints: dict[str, Any] = {}
    # A desk can describe a shelf's height: prioritize its functional purpose.
    if "book" in tokens or "bookshelf" in tokens:
        constraints["category"] = "bookshelf"
    elif "lamp" in tokens or "reading" in tokens:
        constraints["category"] = "lamp"
    elif "chair" in tokens:
        constraints["category"] = "chair"
    elif "storage" in tokens:
        constraints["category"] = "storage"
    elif "desk" in tokens:
        constraints["category"] = "desk"
    for material in ("wood", "mesh", "metal", "plastic", "fabric"):
        if material in tokens:
            constraints["material"] = material
            break
    if re.search(r"(?:not taller than|no taller than|below|under) (?:my |the |a )?desk", lower):
        constraints["max_height_cm"] = 75  # documented ordinary desk assumption
    height = re.search(r"(?:at most|no taller than|not taller than|maximum|max|under|below|<=)\s*(\d+(?:\.\d+)?)\s*cm", lower)
    if height:
        constraints["max_height_cm"] = float(height.group(1))
    capacity = re.search(r"(?:(about|around|at least|minimum|at most)\s+)?(\d+)\s+books", lower)
    if capacity:
        modifier, number = capacity.groups()
        target = int(number)
        if modifier in ("at least", "minimum"):
            constraints["min_capacity_books"] = target
        elif modifier == "at most":
            constraints["max_capacity_books"] = target
        else:
            constraints["capacity_books"] = target
    if "usbc" in tokens:
        constraints["usb_c"] = True
    if "dimmable" in tokens:
        constraints["dimmable"] = True
    if "lumbar" in tokens:
        constraints["lumbar_support"] = True
    if "footrest" in tokens:
        constraints["footrest"] = True
    return constraints


def _matches(item: dict[str, Any], requirements: dict[str, Any]) -> bool:
    attrs = item["attributes"]
    for key, value in requirements.items():
        if key == "category":
            if item["category"] != value:
                return False
        elif key == "max_height_cm":
            if attrs.get("height_cm", math.inf) > value:
                return False
        elif key == "min_capacity_books":
            if attrs.get("capacity_books", -1) < value:
                return False
        elif key == "max_capacity_books":
            if attrs.get("capacity_books", math.inf) > value:
                return False
        elif key == "capacity_books":
            if abs(attrs.get(key, -10000) - value) > max(1, value * 0.1):
                return False
        elif attrs.get(key) != value:
            return False
    return True


class Catalog:
    def __init__(self, path=None, categories=None, price_factor=1.0):
        self.path = Path(path) if path is not None else DEFAULT_PATH
        multiplier = Decimal(str(price_factor))
        if not multiplier.is_finite() or multiplier <= 0:
            raise ValueError("price_factor must be finite and positive")
        allowed = {categories} if isinstance(categories, str) else set(categories or [])
        self.items: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        with self.path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError(f"line {line_number}: product must be an object")
                for field in ("id", "name", "description", "category"):
                    if not isinstance(item.get(field), str) or not item[field].strip():
                        raise ValueError(f"line {line_number}: invalid {field}")
                product_id = item["id"]
                if product_id in seen:
                    raise ValueError(f"line {line_number}: duplicate product ID {product_id}")
                seen.add(product_id)
                reserve = item.get("reserve")
                if (isinstance(reserve, bool) or not isinstance(reserve, (int, float))
                        or not math.isfinite(reserve) or reserve <= 0):
                    raise ValueError(f"line {line_number}: invalid reserve")
                if not isinstance(item.get("attributes"), dict):
                    raise ValueError(f"line {line_number}: attributes must be an object")
                if allowed and item["category"] not in allowed:
                    continue
                # This is shop catalog configuration, not an ask/counteroffer.
                item["reserve"] = int((Decimal(str(reserve)) * multiplier).to_integral_value(rounding=ROUND_CEILING))
                self.items[product_id] = item
        self._terms = {key: Counter(_tokens(" ".join((item["id"], item["name"], item["description"]))))
                       for key, item in self.items.items()}
        document_frequency = Counter(term for words in self._terms.values() for term in words)
        self._idf = {term: math.log(1 + len(self.items) / (1 + count)) for term, count in document_frequency.items()}

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Return at most 32 candidates; never substitute a different constrained item.

        An exact ID/name mentioned in a query ranks first only if it also meets
        explicit physical constraints. Empty/unsupported queries yield no match.
        Copies prevent accidental edits to returned candidates changing reserves.
        """
        if not isinstance(query, str):
            raise TypeError("query must be text")
        limit = min(MAX_CANDIDATES, max(0, int(limit)))
        if not limit or not query.strip():
            return []
        requirements = _constraints(query)
        terms = set(_tokens(query))
        lower = query.lower()
        ranked = []
        for key, item in self.items.items():
            if not _matches(item, requirements):
                continue
            words = self._terms[key]
            score = sum(self._idf.get(term, 0) * (1 + math.log(words[term])) for term in terms if words[term])
            if re.search(r"(?<![\w-])" + re.escape(key.lower()) + r"(?![\w-])", lower):
                score += 100
            if item["name"].lower() in lower:
                score += 50
            if score > 0:
                ranked.append((-score, key))
        ranked.sort()
        return [copy.deepcopy(self.items[key]) for _, key in ranked[:limit]]

    def describe_categories(self) -> list[str]:
        return sorted({item["category"] for item in self.items.values()})
