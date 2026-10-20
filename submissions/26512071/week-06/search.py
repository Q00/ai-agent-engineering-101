"""Narrow catalog.json to a few candidates for a buyer's description (challenge 1).

    python search.py "something wooden to keep about 40 books, not taller than my desk"

Plain BM25 over each item's name and description, standard library only.
At most PER_KIND candidates come from one kind, so a word that only describes the
buyer's room ("not taller than my desk") cannot fill the list with desk lamps.
An exact item name (the trade test's "lamp") skips the search.

BM25 cannot read numbers: with "40 books, not taller than my desk" none of the 58
items that fit made the top 20. So the seller first has the harness turn the request
into a spec (kinds, height, width, books, seats, wooden), and search() keeps only
the items whose numbers, parsed from their descriptions, fit the spec before ranking.
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

K1, B = 1.5, 0.75
WOODS = ("wood", "pine", "oak", "walnut", "birch", "bamboo", "rattan", "beech")
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


def attrs(description):
    """The numbers a buyer may ask about, read from a description (None if absent)."""
    def grab(pattern):
        m = re.search(pattern, description)
        return int(m.group(1)) if m else None
    return {"height": grab(r"(\d+) cm tall"), "width": grab(r"(\d+) cm wide"),
            "books": grab(r"about (\d+) books"), "seats": grab(r"[Ss]eats (\d+)"),
            "wooden": any(w in description.lower() for w in WOODS)}


class Catalog:
    def __init__(self, path=Path(__file__).with_name("catalog.json")):
        self.items = json.loads(Path(path).read_text())
        self.by_name = {i["name"].lower(): i for i in self.items}
        self.docs = [Counter(tokens(i["name"] + " " + i["description"])) for i in self.items]
        self.avg = sum(sum(d.values()) for d in self.docs) / len(self.docs)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.kinds = sorted({i["kind"] for i in self.items})
        self.attrs = [attrs(i["description"]) for i in self.items]

    def fits(self, a, kind, spec):
        """Does an item with these parsed numbers fit the spec? A missing number fits."""
        def num(key):
            v = spec.get(key)
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if spec.get("kinds") and kind not in spec["kinds"]:
            return False
        if spec.get("wooden") is True and not a["wooden"]:
            return False
        if num("max_height_cm") and a["height"] and a["height"] > num("max_height_cm"):
            return False
        if num("min_height_cm") and a["height"] and a["height"] < num("min_height_cm"):
            return False
        if num("max_width_cm") and a["width"] and a["width"] > num("max_width_cm"):
            return False
        if num("books") and a["books"] and not 0.8 * num("books") <= a["books"] <= 1.5 * num("books"):
            return False  # "about 40 books": 32 to 60
        if num("seats") and a["seats"] and a["seats"] < num("seats"):
            return False
        return True

    def exact(self, text):
        """The item the text is nothing but a request for, e.g. 'I want to buy a lamp'.

        Only the whole request counts: 'a chair I can sit in all day' mentions 'chair'
        but describes something else, so it goes to the search.
        """
        words = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
        words = re.sub(r"^(i want to buy|i d like to buy|i want|i d like|buy)\s+", "", words)
        words = re.sub(r"^(a|an|one|the)\s+", "", words)
        return self.by_name.get(words)

    def search(self, text, k=20, spec=None):
        """Top k items for the text. With a spec, only items that fit it are ranked;
        if none fits, the spec is dropped rather than returning nothing."""
        q = tokens(text)
        pool = list(zip(self.items, self.docs, self.attrs))
        if spec:
            fit = [x for x in pool if self.fits(x[2], x[0]["kind"], spec)]
            pool = fit or pool
        scored = []
        for item, d, _ in pool:
            length = sum(d.values())
            s = sum(self.idf.get(t, 0) * d[t] * (K1 + 1) / (d[t] + K1 * (1 - B + B * length / self.avg))
                    for t in q if t in d)
            if s > 0 or spec:
                scored.append((s, item))
        scored.sort(key=lambda x: -x[0])
        cap = k if spec and spec.get("kinds") else PER_KIND  # the spec already chose the kinds
        out, per = [], Counter()
        for _, item in scored:
            if per[item["kind"]] < cap:
                per[item["kind"]] += 1
                out.append(item)
            if len(out) == k:
                break
        return out


if __name__ == "__main__":
    cat = Catalog()
    query = " ".join(sys.argv[1:]) or "something wooden to keep about 40 books, not taller than my desk"
    spec = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--spec" else None
    if spec:
        query = " ".join(sys.argv[3:])
    hit = cat.exact(query)
    if hit:
        print("exact:", hit["id"], hit["name"], hit["reserve"])
    for item in cat.search(query, spec=spec):
        print(f"{item['id']}  {item['reserve']:>5}  {item['description']}")
