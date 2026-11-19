"""Catalog search: narrows thousands of items to a few candidates before the harness sees them.

The harness never gets the whole catalog. For a buyer's description the code scores every item and
hands the top TOP_K (id, name, description, reserve) to the harness, which picks one or asks a
clarifying question. Scoring is plain and inspectable:

  - word overlap between the request and the item's name, kind, category and description,
    weighted by how rare the word is in the catalog (IDF), with a few synonyms
    ("sit" -> chair/stool/sofa, "wooden" -> oak/pine/walnut/..., "books" -> bookshelf ...)
  - numbers in the request ("about 40 books", "under 80 cm") pull items whose matching attribute
    is close; "not taller than my desk" / "low" prefer items no higher than LOW_CM
"""
import json
import math
import re
from collections import Counter

TOP_K = 10
LOW_CM = 80     # "not taller than my desk": desks are 70-78 cm here

SYNONYMS = {
    "sit": ["chair", "stool", "sofa", "armchair"], "seat": ["chair", "stool", "sofa"],
    "wooden": ["oak", "pine", "walnut", "birch", "teak", "beech", "wood"], "wood": ["oak", "pine", "walnut", "birch", "teak", "beech"],
    "metal": ["steel", "brass", "aluminium", "iron"], "books": ["bookshelf", "shelf", "shelves"], "book": ["bookshelf", "shelf"],
    "light": ["lamp"], "lighting": ["lamp"], "read": ["reading", "lamp"], "reading": ["lamp"],
    "keep": ["shelf", "storage", "cabinet"], "store": ["shelf", "storage", "cabinet", "wardrobe"],
    "clothes": ["wardrobe"], "shoes": ["shoe", "rack"], "sleep": ["bed"], "work": ["desk", "office"],
    "office": ["desk", "chair"], "couch": ["sofa"], "table": ["table", "desk"], "eat": ["dining", "table"],
    "wheels": ["wheels"], "rolling": ["wheels"], "carpet": ["rug"],
}
STOP = set("a an the i me my to for of and or with in on at it is be want buy need some something about around "
           "than not no that this please would like looking can you have do does any into just "
           # greetings, contractions and shop talk, so "Hi, I'd like to purchase a lamp" still names the lamp
           "hi hello hey thanks thank d ll m re ve s t purchase get got interested one am we us our "
           "how much what price cost costs your there sell best good great deal offer quote".split())
NUM_ATTRS = {"books": "holds_books", "book": "holds_books", "cm": "height_cm", "seats": "seats", "people": "seats",
             "shelves": "shelves", "kg": "max_load_kg"}


def tokens(text: str) -> list:
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        w = w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
        if w not in STOP:
            out.append(w)
    return out


class Catalog:
    def __init__(self, path: str):
        data = json.load(open(path, encoding="utf-8"))
        self.name = data.get("name", "Shop")
        self.description = data.get("description", "")   # the card text, if the catalog sets one
        self.items = data["items"]
        self.by_id = {i["id"]: i for i in self.items}
        self.docs = [set(tokens(f"{i['name']} {i['kind']} {i['category']} {i['description']}")) for i in self.items]
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log(len(self.items) / (1 + n)) + 1 for t, n in df.items()}

    def categories(self) -> list:
        return sorted({i["category"] for i in self.items})

    def kinds(self) -> list:
        return sorted({i["kind"] for i in self.items})

    def exact(self, text: str):
        """The item the text names and nothing more, e.g. 'I want to buy a lamp' -> lamp.

        Only when the request has no words besides the name (and filler like "I want to buy a"):
        "a dimmable lamp for my bedside table" also contains "lamp", but it describes something,
        so it goes to search instead (attempt 1 of challenge 1 sold the standard chair for
        "an office chair with wheels" because of this).
        """
        words = set(tokens(text))
        hits = [i for i in self.items if words and words == set(tokens(i["name"]))]
        return max(hits, key=lambda i: len(i["name"])) if hits else None

    def search(self, text: str, k: int = TOP_K) -> list:
        q = tokens(text)
        expanded = Counter(q)
        for t in q:
            for s in SYNONYMS.get(t, []):
                expanded[s] += 0.6
        nums = [(int(n), unit) for n, unit in re.findall(r"(\d+)\s*([a-z]+)", text.lower())]
        low = bool(re.search(r"not (taller|higher) than (my|a|the) desk|\blow\b|\bshort\b|under (the|my) window", text.lower()))
        scored = []
        for item, doc in zip(self.items, self.docs):
            s = sum(w * self.idf.get(t, 0) for t, w in expanded.items() if t in doc)
            if s <= 0:
                continue
            a = item.get("attrs", {})
            for n, unit in nums:
                key = NUM_ATTRS.get(unit.rstrip("s")) or NUM_ATTRS.get(unit)
                if key and a.get(key):
                    s += 3 * max(0.0, 1 - abs(a[key] - n) / max(n, 1))
            if low and a.get("height_cm"):
                s += 2 if a["height_cm"] <= LOW_CM else -2
            scored.append((s, item))
        scored.sort(key=lambda x: -x[0])
        return [i for _, i in scored[:k]]


def line(item: dict, with_reserve: bool = True) -> str:
    r = f" | reserve {item['reserve']}" if with_reserve else ""
    return f"{item['id']} | {item['name']} | {item['description']}{r}"


if __name__ == "__main__":    # python catalog.py catalog.json "something wooden to keep about 40 books, ..."
    import sys
    c = Catalog(sys.argv[1])
    for it in c.search(sys.argv[2]):
        print(line(it))
