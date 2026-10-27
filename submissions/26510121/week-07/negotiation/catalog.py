"""Local synthetic inventory and bounded, constraint-based retrieval.

The model interprets the request. This module never chooses a negotiation price.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path


CATEGORIES = ("bookshelf", "chair", "lamp", "desk", "cabinet")
MATERIALS = ("wood", "metal", "plastic")
COLORS = ("black", "white", "brown", "blue")


@dataclass(frozen=True)
class Product:
    item_id: str
    name: str
    description: str
    category: str
    material: str
    color: str
    height_cm: int
    width_cm: int
    capacity_books: int
    reserve: int

    def __post_init__(self):
        if self.category not in CATEGORIES or self.material not in MATERIALS or self.color not in COLORS:
            raise ValueError("Invalid inventory attributes")
        if not all(isinstance(v, str) and v.strip() for v in (self.item_id, self.name, self.description)):
            raise ValueError("Missing inventory identity")
        for value in (self.height_cm, self.width_cm, self.reserve):
            if type(value) is not int or value <= 0:
                raise ValueError("Invalid inventory dimension or reserve")
        if type(self.capacity_books) is not int or self.capacity_books < 0:
            raise ValueError("Invalid book capacity")

    def public(self):
        return {key: value for key, value in asdict(self).items() if key != "reserve"}


def matches(product, need):
    """All explicit supported requirements must hold; null means unspecified."""
    row = product.public() if isinstance(product, Product) else product
    for key in ("category", "material", "color"):
        if need.get(key) is not None and row[key] != need[key]:
            return False
    for bound, field, minimum in (("min_books", "capacity_books", True),
                                   ("max_height_cm", "height_cm", False),
                                   ("max_width_cm", "width_cm", False)):
        value = need.get(bound)
        if value is not None and (row[field] < value if minimum else row[field] > value):
            return False
    return True


class Catalog:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.products = {}
        self.by_category = {category: [] for category in CATEGORIES}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            product = Product(**json.loads(line))
            if product.item_id in self.products:
                raise ValueError("Duplicate inventory identity")
            self.products[product.item_id] = product
            self.by_category[product.category].append(product)
        if not self.products:
            raise ValueError("Empty inventory")

    def retrieve(self, need, limit=8):
        if need.get("action") != "search" or need.get("category") not in self.by_category:
            raise ValueError("Validated search intent required")
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("Candidate limit must be 1..8")
        found = [p for p in self.by_category[need["category"]] if matches(p, need)]
        # Prefer compact fits. Ties are stable; reserves never rank candidates.
        found.sort(key=lambda p: (p.capacity_books - (need.get("min_books") or 0),
                                  p.height_cm * p.width_cm, p.item_id))
        return found[:limit], len(found)
