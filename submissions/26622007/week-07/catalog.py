"""Search over the seller's catalog, so the harness never sees the whole catalog.

The harness turns the buyer's words into a query: keywords, an optional category and
numeric filters on known attributes. search() applies the filters in code and ranks
what is left by keyword overlap (IDF-weighted), returning a short candidate list.
"""
import json
import math
import re
from collections import Counter

NUMERIC = ["height_cm", "width_cm", "books", "seats", "drawers", "pairs", "shade_cm", "reserve"]
STOP = {"i", "want", "to", "buy", "a", "an", "the", "please", "would", "like", "need", "some", "something",
        "for", "my", "me", "of", "and", "with", "in", "that", "it", "is", "can", "you", "do", "have", "looking"}


def words(text):
    return re.findall(r"[a-z]+", text.lower())


class Catalog:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.items = json.load(f)
        self.by_id = {i["id"]: i for i in self.items}
        self.by_name = {i["name"].lower(): i for i in self.items}
        self.categories = sorted({i["category"] for i in self.items})
        self.docs = [Counter(words(f"{i['name']} {i['category']} {i['description']}")) for i in self.items]
        df = Counter(w for d in self.docs for w in d)
        self.idf = {w: math.log(len(self.items) / n) for w, n in df.items()}

    def exact(self, text):
        """The buyer named an item and nothing else ("I want to buy a lamp")."""
        rest = " ".join(w for w in words(text) if w not in STOP)
        return self.by_name.get(rest)

    def search(self, keywords, category="", filters=(), k=15):
        pool = range(len(self.items))
        if category:
            pool = [n for n in pool if self.items[n]["category"] == category] or list(range(len(self.items)))
        for f in filters:
            field, op, value = f["field"], f["op"], f["value"]
            keep = []
            for n in pool:
                v = self.items[n].get(field)
                if not isinstance(v, (int, float)):
                    continue
                if (op == "max" and v <= value) or (op == "min" and v >= value):
                    keep.append(n)
            pool = keep
        query = [w for w in words(keywords) if w not in STOP]
        scored = sorted(pool, key=lambda n: -sum(self.idf.get(w, 0) for w in query if w in self.docs[n]))
        return [self.items[n] for n in scored[:k]]

    @staticmethod
    def brief(item):
        """One line for the harness. The reserve is not shown here; the seller prompt gets it separately."""
        attrs = ", ".join(f"{a}={item[a]}" for a in NUMERIC if a in item and a != "reserve")
        return f"{item['id']}: {item['name']}. {item['description']}" + (f" [{attrs}]" if attrs else "")


if __name__ == "__main__":
    c = Catalog("catalog.json")
    print(c.exact("I want to buy a lamp")["id"], c.exact("I want to buy a wooden bookshelf"))
    for it in c.search("wooden bookshelf books", "bookshelf",
                       [{"field": "books", "op": "min", "value": 35}, {"field": "height_cm", "op": "max", "value": 76}], 5):
        print(Catalog.brief(it), "reserve", it["reserve"])
