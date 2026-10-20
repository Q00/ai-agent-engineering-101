"""Narrow catalog.json to a few candidates for a buyer's description (challenge 1).

    python search.py "something wooden to keep about 40 books, not taller than my desk"

Plain BM25 over each item's name and description, standard library only. It only
casts the net: numbers ("40 books", "not taller than my desk") are left to the
harness, which sees the top candidates with their descriptions and picks one.
At most PER_KIND candidates come from one kind, so a word that only describes the
buyer's room ("not taller than my desk") cannot fill the list with desk lamps.
An exact item name (the trade test's "lamp") skips the search.
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

K1, B = 1.5, 0.75
PER_KIND = 5
STOP = {"a", "an", "the", "to", "i", "my", "me", "for", "of", "in", "and", "or", "with", "want",
        "buy", "something", "about", "than", "not", "is", "it", "that", "can", "some", "would", "like"}
ALIAS = {"wooden": "wood", "shelves": "shelf", "couch": "sofa", "light": "lamp", "seat": "chair"}


def tokens(text):
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        w = ALIAS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]  # books -> book, lamps -> lamp
        if w not in STOP:
            out.append(w)
    return out


class Catalog:
    def __init__(self, path=Path(__file__).with_name("catalog.json")):
        self.items = json.loads(Path(path).read_text())
        self.by_name = {i["name"].lower(): i for i in self.items}
        self.docs = [Counter(tokens(i["name"] + " " + i["description"])) for i in self.items]
        self.avg = sum(sum(d.values()) for d in self.docs) / len(self.docs)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def exact(self, text):
        """The item whose name the text names as a whole word, e.g. 'I want to buy a lamp'."""
        words = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
        hits = [i for name, i in self.by_name.items() if f" {name} " in words]
        return max(hits, key=lambda i: len(i["name"]), default=None)

    def search(self, text, k=20):
        q = tokens(text)
        scored = []
        for item, d in zip(self.items, self.docs):
            length = sum(d.values())
            s = sum(self.idf.get(t, 0) * d[t] * (K1 + 1) / (d[t] + K1 * (1 - B + B * length / self.avg))
                    for t in q if t in d)
            if s > 0:
                scored.append((s, item))
        scored.sort(key=lambda x: -x[0])
        out, per = [], Counter()
        for _, item in scored:
            if per[item["kind"]] < PER_KIND:
                per[item["kind"]] += 1
                out.append(item)
            if len(out) == k:
                break
        return out


if __name__ == "__main__":
    cat = Catalog()
    query = " ".join(sys.argv[1:]) or "something wooden to keep about 40 books, not taller than my desk"
    hit = cat.exact(query)
    if hit:
        print("exact:", hit["id"], hit["name"], hit["reserve"])
    for item in cat.search(query):
        print(f"{item['id']}  {item['reserve']:>5}  {item['description']}")
