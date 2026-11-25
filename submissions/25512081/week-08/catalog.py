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


# ---------------------------------------------------------------- structured search
NON_ATTR = {"id", "name", "description", "reserve", "category", "material", "color",
            "style", "tags"}


class StructuredCatalog(Catalog):
    """A large catalog whose items carry attributes the harness can query on.

    Step 1: the harness turns the buyer's words into a query (category,
            materials, colors, numeric min/max, yes/no flags, keywords), using
            only the schema below -- it never sees the items.
    Step 2: the code filters on every constraint; if that leaves fewer than k
            items it relaxes colors, then flags, then materials, then numbers
            (never the category), and ranks the rest by keyword score.
    """

    def __init__(self, items):
        super().__init__(items)
        self.categories = sorted({it["category"] for it in items})
        self.materials = sorted({it["material"] for it in items})
        self.colors = sorted({it["color"] for it in items})
        self.numeric, self.flags = {}, set()
        for it in items:
            for k, v in it.items():
                if k in NON_ATTR:
                    continue
                if isinstance(v, bool):
                    self.flags.add(k)
                elif isinstance(v, (int, float)):
                    lo, hi = self.numeric.get(k, (v, v))
                    self.numeric[k] = (min(lo, v), max(hi, v))
                elif isinstance(v, str):
                    self.flags.add(f"{k}={v}")

    @staticmethod
    def is_structured(items):
        return len(items) > 20 and all("category" in it for it in items[:50])

    def schema_text(self):
        per_cat = {}
        for it in self.items:
            keys = per_cat.setdefault(it["category"], set())
            keys.update(k for k in it if k not in NON_ATTR)
        lines = [f"- {c}: " + ", ".join(sorted(per_cat[c])) for c in self.categories]
        nums = ", ".join(f"{k} {lo}..{hi}" for k, (lo, hi) in sorted(self.numeric.items()))
        return ("Categories and their attributes:\n" + "\n".join(lines)
                + f"\nNumeric ranges: {nums}\nMaterials: {', '.join(self.materials)}"
                + f"\nColors: {', '.join(self.colors)}")

    def query_schema(self):
        num = {k: {"type": "number"} for k in self.numeric}
        return {"type": "object", "properties": {
            "category": {"type": ["string", "null"], "enum": self.categories + [None]},
            "materials": {"type": "array", "items": {"type": "string", "enum": self.materials}},
            "colors": {"type": "array", "items": {"type": "string", "enum": self.colors}},
            "min": {"type": "object", "properties": num, "additionalProperties": False},
            "max": {"type": "object", "properties": num, "additionalProperties": False},
            # an empty enum is not a valid JSON Schema (a shop whose items have no flags)
            "flags_true": {"type": "array", "items": ({"type": "string", "enum": sorted(self.flags)}
                                                      if self.flags else {"type": "string"}),
                           **({} if self.flags else {"maxItems": 0})},
            "keywords": {"type": "string"}},
            "required": ["category", "materials", "colors", "min", "max", "flags_true", "keywords"]}

    def _match(self, it, q, use):
        if q.get("category") and it["category"] != q["category"]:
            return False
        if "materials" in use and q.get("materials") and it["material"] not in q["materials"]:
            return False
        if "colors" in use and q.get("colors") and it["color"] not in q["colors"]:
            return False
        if "flags" in use:
            for f in q.get("flags_true", []):
                k, _, v = f.partition("=")
                if (v and it.get(k) != v) or (not v and it.get(k) is not True):
                    return False
        if "numbers" in use:
            for k, v in (q.get("min") or {}).items():
                if k in it and it[k] < v:
                    return False
            for k, v in (q.get("max") or {}).items():
                if k in it and it[k] > v:
                    return False
        return True

    def query(self, q, k=10):
        """-> (candidates, which constraints were kept, how many matched all)."""
        order = ["colors", "flags", "materials", "numbers"]
        full = [it for it in self.items if self._match(it, q, set(order))]
        use = set(order)
        hits = full
        for drop in order:
            if len(hits) >= k:
                break
            use.discard(drop)
            hits = [it for it in self.items if self._match(it, q, use)]
        rank = {it["id"]: i for i, it in
                enumerate(self.search(q.get("keywords") or "", k=len(self.items)))}
        hits.sort(key=lambda it: (rank.get(it["id"], 10**9), it["reserve"]))
        return hits[:k], sorted(use), len(full)


def load(path=None):
    cat, meta = Catalog.load(path)
    if StructuredCatalog.is_structured(cat.items):
        cat = StructuredCatalog(cat.items)
    return cat, meta
