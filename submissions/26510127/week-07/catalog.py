"""Narrow a large catalog to a few candidates for the harness (challenge 1).

The harness never sees the whole catalog. search() scores every item against the buyer's words
(name, category, material, color, style, description) and returns the top k with their attributes;
the harness then picks one of those k or says none fits.
"""
import json
import re
from pathlib import Path

SYN = {  # buyer word -> catalog words
    "bookcase": ["bookshelf"], "shelf": ["bookshelf"], "shelves": ["bookshelf"], "books": ["bookshelf"],
    "light": ["lamp"], "lighting": ["lamp"], "couch": ["sofa"], "settee": ["sofa"], "seat": ["chair", "stool"],
    "wooden": ["oak", "pine", "walnut", "birch", "bamboo", "wood"], "wood": ["oak", "pine", "walnut", "birch", "bamboo"],
    "metal": ["steel", "aluminium"], "carpet": ["rug"], "dining": ["table"], "closet": ["wardrobe"],
    "dresser": ["cabinet"], "sideboard": ["cabinet"], "workstation": ["desk"], "study": ["desk"],
    "sleep": ["bed"], "bar": ["stool"], "grey": ["gray", "grey", "charcoal"], "gray": ["grey"],
}
STOP = set("a an the to of for and or in on with my me i want need something about some that is it not "
           "than taller shorter wider under over around near approximately buy looking would like please "
           "keep hold holds".split())


def tokens(text):
    return [t for t in re.findall(r"[a-z]+", text.lower()) if t not in STOP]


class Catalog:
    def __init__(self, path):
        self.items = json.loads(Path(path).read_text(encoding="utf-8"))
        self.by_id = {i["id"]: i for i in self.items}
        self._index = []
        for it in self.items:
            words = set(tokens(" ".join([it["name"], it["category"], it["material"], it["color"],
                                         it["style"], it["description"]])))
            if it["material"] in {"oak", "pine", "walnut", "birch", "bamboo"}:
                words.add("wood")
            self._index.append((it, words, set(tokens(it["category"]))))

    def search(self, query, k=8):
        q = tokens(query)
        expanded = []
        for t in q:
            expanded.append(t)
            expanded += SYN.get(t, [])
        ql = query.lower()
        # a number counts for an attribute only when it stands next to it: "40 books", "6 people", "75 cm"
        wants = {f: int(n) for n, f in re.findall(r"(\d+)\s*(books|seats|people)", ql)}
        cm = [int(n) for n in re.findall(r"(\d+)\s*cm", ql)]
        lower = re.search(r"not taller|under|below|lower than|at most|no higher", ql)
        higher = re.search(r"at least|taller than|over|higher than", ql)
        scored = []
        for it, words, cat in self._index:
            s = sum(3 if t in cat else 1 for t in set(expanded) if t in words)
            if not s:
                continue
            for field, n in wants.items():                   # prefer values close to what the buyer said
                if field in it:
                    s += max(0.0, 2 - abs(it[field] - n) / max(n, 1) * 4)
            for n in cm:                                      # a height limit in cm
                if lower and it["height_cm"] <= n or higher and it["height_cm"] >= n:
                    s += 1.5
            scored.append((s, it))
        scored.sort(key=lambda x: -x[0])
        # keep the list varied: at most k//2 candidates per category, so "a lamp for my desk" shows lamps
        # next to desks instead of eight desks
        out, per_cat = [], {}
        for _, it in scored:
            if per_cat.get(it["category"], 0) < max(1, k // 2):
                out.append(it)
                per_cat[it["category"]] = per_cat.get(it["category"], 0) + 1
            if len(out) == k:
                break
        if len(out) < k:                                  # one category only: fill up with its next best
            out += [it for _, it in scored if it not in out][:k - len(out)]
        return out

    @staticmethod
    def brief(it):
        """One line for the harness: everything it needs to judge the fit, no reserve."""
        return f'{it["id"]}: {it["name"]} - {it["description"]}'
