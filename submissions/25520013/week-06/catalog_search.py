"""Narrows a large catalog to a few candidates for the harness: BM25 in pure Python.

    uv run python catalog_search.py "something wooden to keep about 40 books"

The seller never pastes the whole catalog into a prompt. It searches the
buyer's description over each item's name, description and attributes, and
shows the harness only the top few items.

BM25 matches words, not numbers, so "at least 40 books, not taller than 120 cm"
alone ranks every wooden bookshelf alike. A second pass reads a few common
number phrasings ("at least N <unit>", "no taller/wider/longer/deeper than N
cm", "N-inch") into limits on the items' structured attrs, and moves the BM25
top-N items that break fewer limits to the front. Phrasings it does not know
are ignored; the harness still reads every number in the candidates.
"""

import json
import math
import re
import sys
from collections import Counter

K1, B = 1.5, 0.75
STOP = set(
    "a an the and or of for to in on at by with my me i we our is are be it that this "
    "something some about not no than more less at least most up need want looking".split()
)
POOL = 150  # BM25 top-N that the number pass reorders
# Words after "at least N" -> attr; "cm" needs the dimension word after it.
AT_LEAST = {
    "book": "books", "lumen": "lumens", "kg": "max_load_kg", "people": "seats",
    "person": "seats", "drawer": "drawers", "shelf": "shelves", "shelve": "shelves",
    "pair": "pairs", "inch": "max_tv_inch",
}  # fmt: skip
CM_WORD = {
    "tall": "height_cm", "high": "height_cm", "wide": "width_cm", "long": "length_cm",
    "deep": "depth_cm", "off": "seat_height_cm",
}  # fmt: skip
NO_MORE = {
    "taller": "height_cm", "higher": "height_cm", "wider": "width_cm",
    "longer": "length_cm", "deeper": "depth_cm",
}  # fmt: skip
SYNONYMS = {"wooden": "wood", "metallic": "metal", "wicker": "rattan", "couch": "sofa"}


def tokens(text: str) -> list[str]:
    """Lower-cased words without stop words; a crude plural strip and a few synonyms."""
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in STOP:
            continue
        w = SYNONYMS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def limits(query: str) -> list[tuple[str, str, float]]:
    """Reads (attr, ">=" | "<=", value) limits from common number phrasings in the query."""
    q = query.lower()
    out = []
    for n, w1, w2 in re.findall(r"at least (\d+)[ -]?([a-z]+)(?:\s+([a-z]+))?", q):
        w1 = w1[:-1] if w1.endswith("s") and len(w1) > 3 else w1
        if w1 == "cm" and w2 in CM_WORD:
            out.append((CM_WORD[w2], ">=", float(n)))
        elif w1 == "cm" and w2 == "of" and "rail" in q:
            out.append(("hanging_rail_cm", ">=", float(n)))
        elif w1 in AT_LEAST:
            out.append((AT_LEAST[w1], ">=", float(n)))
    for w, n in re.findall(r"(?:no|not) (taller|higher|wider|longer|deeper) than (\d+)", q):
        out.append((NO_MORE[w], "<=", float(n)))
    for n in re.findall(r"(\d+)[ -]inch", q):
        out.append(("max_tv_inch", ">=", float(n)))
    if "wide" in q and "seat at least" in q:  # "a seat at least N cm wide" is the seat, not the piece
        out = [("seat_width_cm", op, v) if a == "width_cm" and op == ">=" else (a, op, v) for a, op, v in out]
    return out


def broken(item: dict, lims: list) -> int:
    """How many limits the item breaks; a missing attr breaks an at-least limit."""
    a, n = dict(item.get("attrs", {})), 0
    if "width_cm" in a and "depth_cm" in a:  # "long" means the longer side of the top or floor
        a["length_cm"] = max(a["width_cm"], a["depth_cm"])
    for attr, op, v in lims:
        x = a.get(attr)
        if op == ">=" and (not isinstance(x, (int, float)) or x < v):
            n += 1
        elif op == "<=" and isinstance(x, (int, float)) and x > v:
            n += 1
    return n


def item_text(item: dict) -> str:
    attrs = " ".join(str(v) for v in item.get("attrs", {}).values())
    return f"{item['name']} {item['description']} {attrs}"


class Index:
    """BM25 index over a list of catalog items."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.docs = [Counter(tokens(item_text(it))) for it in items]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / len(self.lens)
        df = Counter(w for d in self.docs for w in d)
        n = len(items)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query: str, k: int = 15) -> list[dict]:
        """Returns the top-k items with a positive score, best first."""
        q = set(tokens(query))
        scored = []
        for i, (d, ln) in enumerate(zip(self.docs, self.lens)):
            s = 0.0
            for w in q:
                f = d.get(w)
                if f:
                    s += (
                        self.idf[w]
                        * f
                        * (K1 + 1)
                        / (f + K1 * (1 - B + B * ln / self.avg))
                    )
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        pool = [self.items[i] for _, i in scored[:POOL]]
        lims = limits(query)
        pool.sort(key=lambda it: broken(it, lims))  # stable: BM25 order within ties
        return pool[:k]


_INDEXES: dict[int, Index] = {}


def search(items: list[dict], query: str, k: int = 15) -> list[dict]:
    """Top-k items for a free-text query; the index is built once per item list."""
    if id(items) not in _INDEXES:
        _INDEXES[id(items)] = Index(items)
    return _INDEXES[id(items)].search(query, k)


def load(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    for it in search(load("catalog/catalog.json"), " ".join(sys.argv[1:])):
        print(f"{it['id']}  {it['reserve']:>5}  {it['name']}  |  {it['description']}")
