"""Small lexical retrieval layer for the required large-catalog challenge."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    description: str
    reserve: int

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Product":
        return cls(
            str(value["id"]),
            str(value["name"]),
            str(value["description"]),
            int(value["reserve"]),
        )


@dataclass(frozen=True)
class Match:
    product: Product
    score: float


class CatalogIndex:
    """TF-IDF retrieval that keeps thousands of products out of the prompt."""

    def __init__(self, products: list[Product]) -> None:
        if not products:
            raise ValueError("catalog must not be empty")
        self.products = products
        self.term_counts = [Counter(tokens(f"{p.name} {p.description}")) for p in products]
        document_frequency: Counter[str] = Counter()
        for counts in self.term_counts:
            document_frequency.update(counts.keys())
        total = len(products)
        self.idf = {
            term: math.log((total + 1) / (frequency + 1)) + 1
            for term, frequency in document_frequency.items()
        }

    @classmethod
    def load(cls, path: Path) -> "CatalogIndex":
        values = json.loads(path.read_text(encoding="utf-8"))
        return cls.load_values(values)

    @classmethod
    def load_values(cls, values: list[dict[str, object]]) -> "CatalogIndex":
        """Build an index directly from decoded catalog data."""
        return cls([Product.from_dict(value) for value in values])

    def search(self, query: str, limit: int = 5) -> list[Match]:
        query_counts = Counter(tokens(query))
        scored: list[Match] = []
        for product, counts in zip(self.products, self.term_counts):
            score = sum(
                min(counts[term], frequency) * self.idf.get(term, 0)
                for term, frequency in query_counts.items()
            )
            if score > 0:
                scored.append(Match(product, score))
        scored.sort(key=lambda match: (-match.score, match.product.product_id))
        return scored[: max(1, limit)]


def catalog_prompt(query: str, matches: list[Match]) -> str:
    candidates = [
        {
            "id": match.product.product_id,
            "name": match.product.name,
            "description": match.product.description,
            "reserve": match.product.reserve,
            "retrieval_score": round(match.score, 3),
        }
        for match in matches
    ]
    return (
        "Choose the single best product for the buyer description. "
        "Only the retrieval candidates below are available. Return JSON with "
        'action="select" and price=null; put the chosen product id in reason.\n'
        f"Buyer description: {query}\nCandidates: "
        + json.dumps(candidates, ensure_ascii=False)
    )
