"""The shop's catalog: loading a shop's slice, the schema the harness sees, and the search that narrows candidates.

The harness never sees the catalog. It sees schema() (one line per category, ~2.5k chars for 3000 items),
turns the buyer's words into a query, and then sees render() lines for at most K candidates that search() returns.
"""
import json
import math
import random
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
SYNONYM = {"wooden": "wood", "timber": "wood", "metallic": "metal", "bookcase": "bookshelf", "light": "lamp",
           "seat": "chair", "couch": "sofa", "carpet": "rug", "shelves": "shelf", "books": "book"}


def load_shop(cfg):
    """All items of the shop's catalog file, cut to its categories and sample share, at its price factor."""
    items = json.loads((HERE / cfg["catalog"]).read_text(encoding="utf-8"))
    if cfg.get("categories"):
        items = [it for it in items if it["category"] in cfg["categories"]]
    if cfg.get("sample", 1.0) < 1.0:
        rnd = random.Random(cfg.get("seed", 0))
        items = [it for it in items if rnd.random() < cfg["sample"]]
    f = cfg.get("price_factor", 1.0)
    return [dict(it, reserve=max(1, round(it["reserve"] * f))) for it in items]


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        w = SYNONYM.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def doc_text(it):
    return f"{it['name']} {it['type']} {it['category']} {it['material']} {it['group']} {it['description']}"


class Index:
    def __init__(self, items):
        self.items = items
        self.tf = {it["id"]: Counter(tokens(doc_text(it))) for it in items}
        self.len = {k: sum(c.values()) for k, c in self.tf.items()}
        self.avg = sum(self.len.values()) / max(1, len(self.len))
        df = Counter(w for c in self.tf.values() for w in c)
        self.idf = {w: math.log(1 + (len(items) - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def bm25(self, it, q):
        c, L, s = self.tf[it["id"]], self.len[it["id"]], 0.0
        for w in q:
            if w in c:
                s += self.idf[w] * c[w] * 2.2 / (c[w] + 1.2 * (0.25 + 0.75 * L / self.avg))
        return s


def schema(items):
    """One line per category: types, materials, number ranges, flags. Plus the material groups."""
    lines, groups = [], {}
    for cat in sorted({it["category"] for it in items}):
        its = [it for it in items if it["category"] == cat]
        nums = {}
        for it in its:
            for a, v in it["attrs"].items():
                lo, hi = nums.get(a, (v, v))
                nums[a] = (min(lo, v), max(hi, v))
            for m in (it["material"],):
                groups.setdefault(it["group"], set()).add(m)
        flags = sorted({f for it in its for f in it["flags"]})
        lines.append(f"{cat} ({len(its)} items): types {', '.join(sorted({it['type'] for it in its}))}; "
                     f"materials {', '.join(sorted({it['material'] for it in its if it['material']}))}; "
                     f"numbers {', '.join(f'{a} {lo}-{hi}' for a, (lo, hi) in sorted(nums.items()))}"
                     + (f"; flags {', '.join(flags)}" if flags else ""))
    g = "; ".join(f"{k} = {', '.join(sorted(v))}" for k, v in sorted(groups.items()) if k and len(v) > 1)
    return "\n".join(lines) + (f"\nmaterial groups: {g}" if g else "")


def _ok(it, q, use_types, use_mats, use_nums, use_flags):
    if use_types and q.get("types"):
        if not any(str(t).lower() in it["type"] for t in q["types"]):
            return False
    if use_mats and q.get("materials"):
        mats = {SYNONYM.get(str(m).lower(), str(m).lower()) for m in q["materials"]}
        if it["material"] not in mats and it["group"] not in mats:
            return False
    if use_nums:
        for a, v in (q.get("min") or {}).items():
            if a in it["attrs"] and isinstance(v, (int, float)) and it["attrs"][a] < v:
                return False
        for a, v in (q.get("max") or {}).items():
            if a in it["attrs"] and isinstance(v, (int, float)) and it["attrs"][a] > v:
                return False
    if use_flags:
        for f, v in (q.get("flags") or {}).items():
            if f in it["flags"] and it["flags"][f] != bool(v):
                return False
    return True


LEVELS = [("all constraints", True, True, True, True), ("without flags", True, True, True, False),
          ("without numbers", True, True, False, False), ("category only", False, False, False, False)]


def search(index, q, text, k=8):
    """Strictest matches first, then relax one kind of constraint at a time until k candidates. BM25 orders each level."""
    cat = str(q.get("category") or "").lower()
    pool = [it for it in index.items if it["category"] == cat]
    words = tokens(f"{q.get('keywords', '')} {text}")
    picked, used = [], []
    if pool:
        for name, *use in LEVELS:
            hits = [it for it in pool if _ok(it, q, *use) and it not in picked]
            hits.sort(key=lambda it: -index.bm25(it, words))
            if hits:
                used.append(f"{name}: {len(hits)}")
            picked += hits[: k - len(picked)]
            if len(picked) >= k:
                break
    if not picked:  # no usable category: plain BM25 over the whole shop
        hits = sorted(index.items, key=lambda it: -index.bm25(it, words))
        picked = [it for it in hits[:k] if index.bm25(it, words) > 0]
        used.append(f"whole shop by keywords: {len(picked)}")
    return picked, "; ".join(used)


def render(it):
    if not it["attrs"]:
        return f"{it['id']} | {it['name']} | reserve {it['reserve']}"
    attrs = ", ".join(f"{a} {v}" for a, v in it["attrs"].items())
    flags = ", ".join(f"{f} {'yes' if v else 'no'}" for f, v in it["flags"].items())
    return (f"{it['id']} | {it['name']} | {it['type']}, {it['material']} ({it['group']}), {attrs}"
            + (f", {flags}" if flags else "") + f" | reserve {it['reserve']}")
