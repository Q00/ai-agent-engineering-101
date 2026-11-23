"""Catalog loading and search. No model in here: plain code that narrows thousands of
items down to a short list the harness can read in one prompt.

search(category, keywords, constraints) filters by category and numeric constraints
(e.g. {"height_cm": {"max": 75}, "books": {"min": 40}}), then ranks by BM25 over the
name and description.
"""
import json
import math
import re
from collections import Counter


def tokens(text):
    return re.findall(r"[a-z]+|\d+", text.lower())


class Catalog:
    def __init__(self, path, price_factor=1.0, categories=None):
        items = json.load(open(path, encoding="utf-8"))
        if categories:
            items = [i for i in items if i["category"] in categories]
        for i in items:
            i["reserve"] = round(i["reserve"] * price_factor)
        self.items = items
        self.by_id = {i["id"]: i for i in items}
        self.docs = [Counter(tokens(f"{i['name']} {i['category']} {i['description']}")) for i in items]
        self.avg_len = sum(sum(d.values()) for d in self.docs) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in d)
        n = len(items)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def __len__(self):
        return len(self.items)

    def categories(self):
        """category -> sorted attribute names; this is what the harness sees before searching."""
        out = {}
        for i in self.items:
            out.setdefault(i["category"], set()).update(i["attrs"])
        return {c: sorted(a) for c, a in sorted(out.items())}

    def _bm25(self, doc, q, k1=1.2, b=0.75):
        dl = sum(doc.values())
        s = 0.0
        for t in q:
            f = doc.get(t, 0)
            if f:
                s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avg_len))
        return s

    def search(self, category=None, keywords=(), constraints=None, k=10):
        q = tokens(" ".join(keywords))
        hits = []
        for item, doc in zip(self.items, self.docs):
            if category and item["category"] != category:
                continue
            if not self._fits(item, constraints or {}):
                continue
            hits.append((self._bm25(doc, q), item))
        hits.sort(key=lambda h: -h[0])
        return [i for _, i in hits[:k]]

    @staticmethod
    def _fits(item, constraints):
        for attr, rule in constraints.items():
            v = item["attrs"].get(attr)
            if not isinstance(rule, dict):           # {"wooden": true}, {"material": "oak"}
                if v != rule:
                    return False
                continue
            if not isinstance(v, (int, float)):
                return False
            if "min" in rule and v < rule["min"]:
                return False
            if "max" in rule and v > rule["max"]:
                return False
        return True


def brief(item):
    """One line per candidate for the harness prompt. The reserve is NOT in here."""
    a = ", ".join(f"{k}={v}" for k, v in item["attrs"].items())
    return f"{item['id']} | {item['name']} | {item['description']} | {a}"
