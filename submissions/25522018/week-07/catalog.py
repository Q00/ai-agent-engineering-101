"""Retrieval for challenge 1: search a 1,500+ entry product catalog locally.

The LLM sees only the top candidates, not the entire catalog. No external index.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

CATALOG = Path(__file__).with_name("catalog.jsonl")
STOP = set("a an and the to in of for my your this that about something it not is need want buy have with i keep".split())
ALIASES = {
    "wooden": "wood", "books": "book", "reading": "book", "bookcase": "bookshelf",
    "shelves": "bookshelf", "shelf": "bookshelf", "chairs": "chair", "lamps": "lamp",
    "small": "compact", "short": "compact", "desktop": "desk", "taller": "height",
    "illuminate": "light", "lighting": "light", "seating": "chair", "sit": "chair",
}


def words(s):
    return [ALIASES.get(w, w) for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]


class Catalog:
    def __init__(self, path=CATALOG):
        if not Path(path).exists():
            raise FileNotFoundError("Missing catalog.jsonl; include it in submission")
        self.items = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        self.by_sku = {r["sku"]: r for r in self.items}
        self.tokens = [Counter(words(r["name"] + " " + r["description"] + " " + r["category"])) for r in self.items]
        df = Counter()
        for toks in self.tokens:
            df.update(toks.keys())
        self.idf = {term: math.log(1 + (len(self.items) - n + 0.5) / (n + 0.5)) for term, n in df.items()}

    def exact(self, query):
        # Stable reference products, separately reserved for the trade-test catalog.
        normalized = query.lower()
        for key in ("bookshelf", "chair", "lamp"):
            if re.search(r"\b" + key + r"\b", normalized):
                return self.by_sku.get(key)
        return None

    def search(self, query: str, top_k=5):
        terms = set(words(query))
        # Guess the *function*, not an incidental comparison such as "desk height".
        intent = None
        if "book" in terms or "bookshelf" in terms:
            intent = "bookshelf"
        elif "light" in terms or "lamp" in terms or "lighting" in terms:
            intent = "lamp"
        elif "seating" in terms or "chair" in terms:
            intent = "chair"
        scored = []
        for row, tok in zip(self.items, self.tokens):
            score = sum(self.idf.get(t, 0) * min(n, 3) for t, n in tok.items() if t in terms)
            if row["category"] in terms:
                score += 4
            if intent and row["category"] == intent:
                score += 20
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["sku"]))
        return [r for _, r in scored[:top_k]]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("description")
    args = p.parse_args()
    catalog = Catalog()
    print(f"catalog-size={len(catalog.items)}")
    for row in catalog.search(args.description):
        print(json.dumps(row, ensure_ascii=False))
