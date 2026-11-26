"""Bounded catalog retrieval with explicit attribute constraints."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from pydantic import TypeAdapter

from market_a2a.models import Product

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ALIASES: Final = {
    "wooden": "wood",
    "timber": "wood",
    "bookcase": "bookshelf",
    "bookcases": "bookshelf",
    "bookshelves": "bookshelf",
    "books": "bookshelf",
    "seating": "chair",
    "seat": "chair",
    "chairs": "chair",
    "lamps": "lamp",
    "light": "lamp",
    "lighting": "lamp",
    "cabinet": "storage",
    "cupboard": "storage",
    "grey": "gray",
    "compact": "small",
    "adjustable": "adjustable",
    "liter": "litre",
    "liters": "litre",
    "litres": "litre",
}
STOPWORDS: Final = frozenset(
    [
        "a",
        "an",
        "the",
        "i",
        "want",
        "buy",
        "need",
        "something",
        "for",
        "to",
        "my",
        "with",
        "and",
        "of",
        "in",
        "it",
        "is",
        "that",
        "please",
        "about",
        "at",
        "least",
        "more",
        "than",
        "no",
        "not",
        "taller",
        "higher",
        "tall",
        "cm",
        "under",
        "below",
        "maximum",
        "max",
        "height",
        "keep",
        "holding",
        "holds",
        "hold",
        "around",
        "capacity",
    ],
)
COLORS: Final = frozenset(("black", "white", "brown", "blue", "green", "gray"))
MATERIALS: Final = frozenset(("wood", "steel", "bamboo", "aluminum", "plastic"))
KINDS: Final = ("lamp", "chair", "bookshelf", "storage", "desk")


class MissingProductError(LookupError):
    """A requested SKU is absent from this shop's catalog."""

    def __init__(self, sku: str) -> None:
        """Keep traceback mutation available when a context manager propagates."""
        self.sku: str = sku
        super().__init__(sku)

    @override
    def __str__(self) -> str:
        """Expose the unavailable SKU to the caller."""
        return f"Unknown product SKU: {self.sku}"


class CatalogDataError(ValueError):
    """Catalog data cannot safely identify or price a product."""

    def __init__(self, detail: str) -> None:
        """Retain the reason and the standard exception traceback behavior."""
        self.detail: str = detail
        super().__init__(detail)

    @override
    def __str__(self) -> str:
        """Explain the invalid catalog boundary."""
        return self.detail


def tokens(text: str) -> frozenset[str]:
    """Normalize descriptive words without requiring an exact product name."""
    return (
        frozenset(
            ALIASES.get(found[0], found[0])
            for found in re.finditer(r"[a-z]+", text.lower())
        )
        - STOPWORDS
    )


@dataclass(frozen=True, slots=True)
class Constraints:
    """Extract searchable attribute limits from a natural-language request."""

    words: frozenset[str]
    kind: str | None
    max_height: int | None
    min_capacity: int | None

    @classmethod
    def parse(cls, query: str) -> Constraints:
        """Parse common explicit size and capacity phrases before ranking."""
        words = tokens(query)
        kind = next((value for value in KINDS if value in words), None)
        height = re.search(
            r"""(?:(?:no|not)\s+taller\s+than|at\s+most|no\s+higher\s+than|
                under|below|maximum|max|<=)\s*(\d+)\s*cm""",
            query.lower(),
            re.VERBOSE,
        )
        capacity = re.search(
            r"(\d+)\s*(?:books|litres?|liters?|monitors)", query.lower()
        )
        max_height = int(height[1]) if height else None
        if max_height is None and re.search(
            r"(?:not|no) taller than my desk", query.lower()
        ):
            max_height = 75
        return cls(words, kind, max_height, int(capacity[1]) if capacity else None)

    def matches(self, product: Product) -> bool:
        """Prevent ranking from overriding an explicit request constraint."""
        return (
            (self.kind is None or product.kind == self.kind)
            and (not self.words & COLORS or product.color in self.words & COLORS)
            and (
                not self.words & MATERIALS or product.material in self.words & MATERIALS
            )
            and (self.max_height is None or product.height_cm <= self.max_height)
            and (self.min_capacity is None or product.capacity >= self.min_capacity)
        )


class Catalog:
    """Load a typed shop catalog and rank only a bounded candidate shortlist."""

    products: tuple[Product, ...]
    _by_sku: dict[str, Product]
    _terms: dict[str, frozenset[str]]
    _weights: dict[str, float]

    def __init__(
        self, path: Path, price_offset: int = 0, kinds: tuple[str, ...] | None = None
    ) -> None:
        """Parse committed records and apply a shop's inventory and price profile."""
        parsed = TypeAdapter(tuple[Product, ...]).validate_json(path.read_bytes())
        if len({item.sku for item in parsed}) != len(parsed):
            detail = "Duplicate catalog SKU"
            raise CatalogDataError(detail)
        selected = tuple(item for item in parsed if kinds is None or item.kind in kinds)
        if any(item.reserve + price_offset < 1 for item in selected):
            detail = "Price offset produces a nonpositive reserve"
            raise CatalogDataError(detail)
        self.products = tuple(
            item.model_copy(update={"reserve": item.reserve + price_offset})
            for item in selected
        )
        self._by_sku = {item.sku: item for item in self.products}
        self._terms = {
            item.sku: tokens(item.name)
            | tokens(item.description)
            | frozenset((item.kind, item.material, item.color))
            for item in self.products
        }
        counts = Counter(word for words in self._terms.values() for word in words)
        self._weights = {
            word: math.log1p(len(self.products) / count)
            for word, count in counts.items()
        }

    def get(self, sku: str) -> Product:
        """Resolve a SKU or raise a typed missing-product error."""
        try:
            return self._by_sku[sku]
        except KeyError as error:
            raise MissingProductError(sku) from error

    def search(
        self,
        query: str,
        limit: int = 6,
        *,
        constraints: Callable[[Product], bool] | None = None,
    ) -> tuple[Product, ...]:
        """Apply request constraints before ranking a bounded candidate set."""
        parsed = Constraints.parse(query)
        scored: list[tuple[float, str, Product]] = []
        for item in self.products:
            if not parsed.matches(item) or (
                constraints is not None and not constraints(item)
            ):
                continue
            overlap = parsed.words & self._terms[item.sku]
            if not overlap:
                continue
            score = sum(self._weights[word] for word in overlap)
            if item.sku == item.kind and parsed.words == frozenset((item.kind,)):
                score += 100
            scored.append((score, item.sku, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return tuple(item for _, _, item in scored[: max(0, min(limit, 6))])
