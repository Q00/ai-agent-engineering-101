"""Catalog loading and candidate search for the seller.

The harness never sees a whole catalog. The code narrows a buyer's request to a
handful of candidates and the harness picks one of those (or none):

  exact name   the request names an item outright ("I want to buy a lamp")
               -> that item, no harness call (the trade test takes this path)
  search       otherwise, score items by overlap between the request and each
               item's name/description/tags (with a few synonyms and stems),
               and keep the top K
"""
import json
import math
import re
from collections import Counter

DEFAULT_ITEMS = [
    {"id": "lamp", "name": "lamp", "reserve": 100,
     "description": "A desk lamp with an adjustable arm."},
    {"id": "chair", "name": "chair", "reserve": 180,
     "description": "A sturdy wooden chair."},
    {"id": "bookshelf", "name": "bookshelf", "reserve": 260,
     "description": "A five-shelf wooden bookshelf."},
]

STOP = set("a an the i im i'm want to buy for of and or with my me some something "
           "that is it be can could would like need looking please about not than "
           "in on at my your our this one get have has".split())
SYNONYMS = {
    "books": ["bookshelf", "bookcase", "shelf", "shelving"], "book": ["bookshelf", "bookcase"],
    "light": ["lamp", "lighting"], "lighting": ["lamp"], "reading": ["lamp"],
    "seat": ["chair", "stool", "bench"], "sit": ["chair", "stool", "sofa"],
    "sleep": ["bed", "mattress"], "wooden": ["wood", "oak", "pine", "walnut", "teak",
                                            "birch", "maple", "bamboo"],
    "wood": ["oak", "pine", "walnut", "teak", "birch", "maple"],
    "metal": ["steel", "iron", "aluminum"], "clothes": ["wardrobe", "dresser"],
    "work": ["desk", "office"], "computer": ["desk"], "eat": ["table", "dining"],
    "floor": ["rug", "carpet"], "plants": ["planter"], "plant": ["planter"],
}


def _tokens(text):
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


class Catalog:
    def __init__(self, items):
        self.items = items
        self.by_id = {it["id"]: it for it in items}
        self.docs = {}
        df = Counter()
        for it in items:
            words = _tokens(" ".join([it["name"], it.get("description", ""),
                                      " ".join(it.get("tags", []))]))
            self.docs[it["id"]] = (set(_tokens(it["name"])), Counter(words))
            df.update(set(words))
        n = max(len(items), 1)
        self.idf = {w: math.log(1 + n / c) for w, c in df.items()}

    @classmethod
    def load(cls, path=None):
        if not path:
            return cls(DEFAULT_ITEMS), {}
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, list):
            return cls(data), {}
        return cls(data["items"]), {k: v for k, v in data.items() if k != "items"}

    def exact(self, text):
        """The item whose full name appears in the request (longest wins)."""
        low = " " + re.sub(r"[^a-z0-9 ]", " ", text.lower()) + " "
        hits = [it for it in self.items if f" {it['name'].lower()} " in low]
        return max(hits, key=lambda it: len(it["name"])) if hits else None

    def search(self, text, k=10):
        q = _tokens(text)
        expanded = Counter(q)
        for w in q:
            for s in SYNONYMS.get(w, []):
                expanded[s] += 0.6
        scored = []
        for it in self.items:
            name_words, words = self.docs[it["id"]]
            s = 0.0
            for w, wt in expanded.items():
                if w in words:
                    s += wt * self.idf.get(w, 1.0) * (2.0 if w in name_words else 1.0)
            if s > 0:
                scored.append((s, it["id"]))
        scored.sort(reverse=True)
        return [self.by_id[i] for _, i in scored[:k]]

    def candidates(self, text, k=10):
        """Small catalogs: everything. Large ones: search."""
        return list(self.items) if len(self.items) <= k else self.search(text, k)
