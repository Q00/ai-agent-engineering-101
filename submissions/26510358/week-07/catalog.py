"""Small, deterministic retrieval layer; the harness makes the final choice.

Only a short candidate list is sent to the model. Explicit material, height,
and book-capacity requirements are filters rather than relevance suggestions.
No counterpart's message can change a product's reserve or attributes.
"""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

DEFAULT_CATALOG = Path(__file__).resolve().parent / "data" / "catalog.json"

CATEGORY_TERMS = {
    "lamp": ("lamp", "lamps", "lighting", "desk light", "램프", "조명", "스탠드"),
    "chair": ("chair", "chairs", "seating", "의자"),
    "bookshelf": (
        "bookshelf", "bookshelves", "bookcase", "bookcases", "shelf", "shelves",
        "book", "books", "책장", "책꽂이", "서가", "책", "선반",
    ),
    "desk": ("desk", "desks", "writing desk", "책상"),
    "table": ("table", "tables", "dining", "테이블", "식탁"),
    "cabinet": ("cabinet", "cabinets", "cupboard", "수납장", "찬장"),
    "sofa": ("sofa", "sofas", "couch", "소파"),
    "bed": ("bed", "beds", "침대"),
    "stool": ("stool", "stools", "스툴"),
    "bench": ("bench", "benches", "벤치"),
}

MATERIAL_TERMS = {
    "wood": ("wood", "wooden", "timber", "oak", "pine", "walnut", "나무", "목재", "원목"),
    "metal": ("metal", "metallic", "steel", "aluminum", "aluminium", "금속", "철제"),
    "bamboo": ("bamboo", "대나무"),
    "plastic": ("plastic", "플라스틱"),
    "glass": ("glass", "유리"),
    "composite": ("composite", "합성목", "합성"),
    "fabric": ("fabric", "textile", "패브릭", "직물"),
    "leather": ("leather", "가죽"),
    "ceramic": ("ceramic", "세라믹", "도자기"),
}

STOP_WORDS = {
    "a", "an", "the", "i", "want", "to", "buy", "for", "of", "and", "or",
    "with", "something", "keep", "about", "than", "my", "not", "no", "at",
    "most", "least", "height", "tall", "taller", "higher", "cm", "books",
    "book", "이하", "미만", "높이", "물건", "있는", "필요한", "정도", "권",
}


def load_catalog(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read a committed catalog, rejecting malformed or duplicate products."""
    source = Path(path) if path is not None else DEFAULT_CATALOG
    items = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("catalog must be a JSON list")
    seen: set[str] = set()
    required = {"id", "name", "description", "reserve", "category", "attributes"}
    for item in items:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("every product needs id, name, description, reserve, category, attributes")
        product_id = item["id"]
        if not isinstance(product_id, str) or not product_id or product_id in seen:
            raise ValueError("product ids must be nonempty and unique")
        seen.add(product_id)
        reserve = item["reserve"]
        if isinstance(reserve, bool) or not isinstance(reserve, (int, float)) or not math.isfinite(reserve) or reserve <= 0:
            raise ValueError(f"invalid reserve for {product_id}")
        if not isinstance(item["attributes"], dict):
            raise ValueError(f"invalid attributes for {product_id}")
    return items


def _contains(text: str, term: str) -> bool:
    # Word boundaries avoid treating "bed" as a substring of another word.
    if term.isascii():
        return re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text) is not None
    return term in text


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z][a-z0-9-]*|[가-힣]+", text.casefold())
        if token not in STOP_WORDS
    }


def _requirements(query: str) -> dict[str, Any]:
    # A reference desk in a height comparison is not another requested product.
    category_text = re.split(r"(?:not|no)\s+(?:taller|higher)\s+than", query, maxsplit=1)[0]
    category_text = re.sub(r"책상(?:보다|\s*높이(?:보다|\s*이하)).*", "", category_text)
    categories = {
        category for category, terms in CATEGORY_TERMS.items()
        if any(_contains(category_text, term) for term in terms)
    }
    materials: set[str] = set()
    excluded_materials: set[str] = set()
    for material, terms in MATERIAL_TERMS.items():
        for term in terms:
            if not _contains(query, term):
                continue
            negative = (
                re.search(r"(?:no|not|without)\s+" + re.escape(term) + r"\b", query)
                or re.search(re.escape(term) + r"\s*(?:제외|말고|아닌)", query)
            )
            (excluded_materials if negative else materials).add(material)

    requirements: dict[str, Any] = {
        "categories": categories,
        "materials": materials,
        "excluded_materials": excluded_materials,
    }
    number = r"(\d+(?:\.\d+)?)"
    unit = r"\s*(?:cm|센티미터)"
    height_max: list[float] = []
    height_min: list[float] = []
    # Generic centimetre comparisons mean height only when no width/depth
    # requirement is present; dimension-specific phrases still work below.
    has_other_dimension = bool(re.search(r"\b(?:width|wide|depth|deep)\b|너비|폭|깊이", query))
    if not has_other_dimension:
        for match in re.finditer(
            r"(?:not\s+(?:taller|higher)\s+than|no\s+(?:taller|higher)\s+than|at\s+most|"
            r"max(?:imum)?(?:\s+height)?|<=|≤)\s*" + number + unit, query,
        ):
            height_max.append(float(match.group(1)))
        for match in re.finditer(r"(?:under|below|less\s+than)\s*" + number + unit, query):
            height_max.append(float(match.group(1)) - 1e-6)
        for match in re.finditer(number + unit + r"\s*(?:이하|(?:or\s+)?less|(?:or\s+)?shorter)", query):
            height_max.append(float(match.group(1)))
        for match in re.finditer(number + unit + r"\s*(?:미만|보다\s*낮|보다\s*작)", query):
            height_max.append(float(match.group(1)) - 1e-6)
        for match in re.finditer(r"(?:at\s+least|min(?:imum)?(?:\s+height)?|>=|≥)\s*" + number + unit, query):
            height_min.append(float(match.group(1)))
        for match in re.finditer(number + unit + r"\s*이상", query):
            height_min.append(float(match.group(1)))
        for match in re.finditer(r"(?:over|above|more\s+than)\s*" + number + unit, query):
            height_min.append(float(match.group(1)) + 1e-6)
    for match in re.finditer(r"(?:height|높이(?:가)?)\s*(?:[:=]|is|of)?\s*(?:<=|≤)\s*" + number + unit, query):
        height_max.append(float(match.group(1)))
    for match in re.finditer(r"(?:height|높이(?:가)?)\s*(?:[:=]|is|of)?\s*(?:>=|≥)\s*" + number + unit, query):
        height_min.append(float(match.group(1)))
    if height_max:
        requirements["height_max"] = min(height_max)
    if height_min:
        requirements["height_min"] = max(height_min)

    # "40 books" describes storage needed, so at least 40 must fit. Capacity
    # is not inferred from arbitrary numbers elsewhere in a buyer's message.
    capacities = [
        int(match.group(1)) for match in re.finditer(r"(\d+)\s*(?:books?\b|권)", query)
    ]
    if capacities:
        requirements["capacity_min"] = max(capacities)
        requirements["categories"].add("bookshelf")
    return requirements


def search_catalog(query: str, items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Retrieve feasible candidates without making the harness's item decision.

    Supports Korean/English category and material synonyms, explicit height
    bounds in centimetres, and minimum book capacity. An unspecified desk
    height is not guessed. Returned products preserve the catalog schema and
    are deep copies so callers cannot alter the shared catalog by accident.
    """
    normalized = str(query).strip().casefold()
    if not normalized or limit <= 0:
        return []
    constraints = _requirements(normalized)
    query_tokens = _tokens(normalized)
    ranked: list[tuple[float, float, str, dict[str, Any]]] = []
    for item in items:
        attrs = item.get("attributes", {})
        category = item.get("category", "")
        material = str(attrs.get("material", "")).casefold()
        if constraints["categories"] and category not in constraints["categories"]:
            continue
        if constraints["materials"] and material not in constraints["materials"]:
            continue
        if material in constraints["excluded_materials"]:
            continue
        height = attrs.get("height_cm")
        if "height_max" in constraints and (not isinstance(height, (int, float)) or height > constraints["height_max"]):
            continue
        if "height_min" in constraints and (not isinstance(height, (int, float)) or height < constraints["height_min"]):
            continue
        capacity = attrs.get("capacity_books")
        if "capacity_min" in constraints and (not isinstance(capacity, (int, float)) or capacity < constraints["capacity_min"]):
            continue
        haystack = " ".join((item.get("id", ""), item.get("name", ""), item.get("description", "")))
        overlap = len(query_tokens & _tokens(haystack))
        semantic_match = bool(constraints["categories"] or constraints["materials"])
        if overlap == 0 and not semantic_match:
            continue
        score = float(overlap)
        if category in constraints["categories"]:
            score += 8
        if material in constraints["materials"]:
            score += 4
        if "capacity_min" in constraints:
            # Prefer sufficient capacity closest to what was requested.
            score += 2 / (1 + (capacity - constraints["capacity_min"]) / 10)
        if "height_max" in constraints:
            score += 1 / (1 + abs(height - constraints["height_max"]) / 10)
        if _contains(normalized, str(item["id"])):
            # Exact base IDs stay first for the prescribed lamp/chair/bookshelf
            # tests, provided the product also meets the explicit constraints.
            score += 100
        ranked.append((-score, float(item["reserve"]), str(item["id"]), item))
    ranked.sort(key=lambda result: result[:3])
    return [copy.deepcopy(result[3]) for result in ranked[:limit]]
