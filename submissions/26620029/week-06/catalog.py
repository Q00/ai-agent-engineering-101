"""Catalog loading, per-shop subsets and the search the seller runs before the harness sees anything.

The harness never sees the whole catalog (2995 items, ~1 MB). It sees:
  1. the list of category names, when it turns the buyer's description into a query;
  2. at most TOP_K candidate lines that survive search() in code, when it picks one.
"""
import json
import random
from pathlib import Path

CATALOG = Path(__file__).with_name("catalog.json")
TOP_K = 8
WOODS = {"oak", "pine", "walnut", "birch", "bamboo"}


def load_shop(cfg=None):
    """Items this shop sells. cfg None -> the whole catalog at catalog prices.

    cfg: {"name", "categories": [...], "factor": 1.1, "keep": 0.5, "seed": 3}
    keeps a seeded fraction of each listed category and scales prices by factor.
    """
    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not cfg:
        return items
    r = random.Random(cfg.get("seed", 0))
    out = []
    for it in items:
        if it["sku"].startswith("BASE-") or it["category"] not in cfg["categories"]:
            continue
        if r.random() > cfg.get("keep", 1.0):
            continue
        it = dict(it)
        it["reserve"] = int(round(it["reserve"] * cfg.get("factor", 1.0)))
        it["list_price"] = int(round(it["list_price"] * cfg.get("factor", 1.0)))
        out.append(it)
    return out


def _materials(names):
    out = set()
    for m in names or []:
        m = str(m).lower()
        out |= WOODS if m in ("wood", "wooden", "solid wood", "timber") else {m}
    return out


def search(items, q):
    """Filter by the structured query q, relaxing soft constraints if nothing is left.

    Returns (candidates, relaxed) where relaxed lists the constraints that were dropped.
    """
    cats = q.get("categories") or ([q["category"]] if q.get("category") else [])
    cats = {str(c).lower() for c in cats if c}
    mats, excl = _materials(q.get("materials")), _materials(q.get("exclude_materials"))
    feats = [str(f).lower() for f in q.get("features") or []]
    words = [str(w).lower() for w in q.get("keywords") or []]

    def num(k):
        try:
            return float(q[k]) if q.get(k) is not None else None
        except (TypeError, ValueError):
            return None

    hard = [  # (name, predicate); dropped from the end when nothing matches
        ("category", lambda it: not cats or it["category"] in cats),
        ("exclude_materials", lambda it: it["material"] not in excl),
        ("materials", lambda it: not mats or it["material"] in mats),
        ("min_capacity", lambda it: num("min_capacity") is None or (it["capacity"] or 0) >= num("min_capacity")),
        ("max_height_cm", lambda it: num("max_height_cm") is None or it["height_cm"] <= num("max_height_cm")),
        ("min_height_cm", lambda it: num("min_height_cm") is None or it["height_cm"] >= num("min_height_cm")),
        ("size", lambda it: not q.get("size") or it["size"] == str(q["size"]).replace(" ", "")),
    ]
    relaxed = []
    while True:
        hits = [it for it in items if all(p(it) for _, p in hard)]
        if hits or len(hard) <= 1:
            break
        relaxed.append(hard.pop()[0])
    # features rank softly: a requested word the catalog never uses ("ergonomic") must not
    # wipe out one it does use ("lumbar"). Keep only the items matching the most features.
    nfeat = lambda it: sum(any(f in x.lower() for x in it["features"]) for f in feats)
    best = max((nfeat(it) for it in hits), default=0)
    if feats and best:
        hits = [it for it in hits if nfeat(it) == best]
    elif feats:
        relaxed.append("features")
    score = lambda it: sum(w in (it["description"] + " " + it["name"]).lower() for w in words)
    hits.sort(key=lambda it: (-score(it), it["reserve"]))
    return hits[:TOP_K], relaxed


def line(it, with_reserve=True):
    """One compact candidate line for a prompt."""
    s = f"{it['sku']} | {it['name']} | {it['description']}"
    return s + (f" | list {it['list_price']} | reserve {it['reserve']}" if with_reserve else "")


def categories(items):
    return sorted({it["category"] for it in items})
