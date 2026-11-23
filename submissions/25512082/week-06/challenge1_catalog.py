"""Validated local retrieval for the synthetic Challenge 1 catalog."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CATALOG = Path(__file__).resolve().parent / "catalog" / "challenge1_catalog.jsonl"
TOKEN_RE = re.compile(r"[a-z]+|\d+")
ALIASES = {
    "wooden": "wood", "timber": "wood", "bookcase": "bookshelf",
    "shelving": "bookshelf", "keep": "stores", "store": "stores",
    "short": "low", "smaller": "low", "under": "below",
}


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    description: str
    reserve: int


def tokens(text: str) -> tuple[str, ...]:
    values = TOKEN_RE.findall(text.lower())
    return tuple(ALIASES.get(value, value) for value in values)


class CatalogError(RuntimeError):
    pass


class CatalogIndex:
    def __init__(self, path: Path = DEFAULT_CATALOG) -> None:
        products: list[Product] = []
        seen: set[str] = set()
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    raw = json.loads(line)
                    product = Product(raw["id"], raw["name"], raw["description"], raw["reserve"])
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise CatalogError(f"invalid catalog row {line_number}") from exc
                if (not product.id or not product.name or not product.description
                        or isinstance(product.reserve, bool) or not isinstance(product.reserve, int)
                        or product.reserve <= 0 or product.id in seen):
                    raise CatalogError(f"invalid catalog row {line_number}")
                seen.add(product.id)
                products.append(product)
        if len(products) < 1000:
            raise CatalogError("Challenge 1 catalog must contain at least 1000 products")
        self.products = tuple(products)
        self.by_id = {product.id: product for product in products}
        self._documents = tuple(Counter(tokens(f"{p.name} {p.description}")) for p in products)
        frequencies = Counter(token for document in self._documents for token in document)
        self._idf = {
            token: math.log((len(products) + 1) / (count + 1)) + 1
            for token, count in frequencies.items()
        }

    def search(self, query: str, limit: int = 8) -> tuple[Product, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        query_terms = Counter(tokens(query))
        if not query_terms:
            return ()
        ranked = []
        for product, document in zip(self.products, self._documents):
            name_terms = Counter(tokens(product.name))
            score = sum(
                count * self._idf.get(term, 0.0) * (3 if term in name_terms else 1)
                for term, count in query_terms.items() if term in document
            )
            if score:
                ranked.append((score, product.id, product))
        ranked.sort(key=lambda value: (-value[0], value[1]))
        return tuple(value[2] for value in ranked[:limit])

    @staticmethod
    def require_candidate(selected_id: str, candidates: tuple[Product, ...]) -> Product:
        selected = next((product for product in candidates if product.id == selected_id), None)
        if selected is None:
            raise CatalogError("model selected an item outside the retrieved candidates")
        return selected
