"""Going prices in Round 3, from the public ledger, and what the forum says about them.

    python prices_r3.py --state .market      # writes .market/prices_r3.json and prints it

Many agents post price notes on the forum ("[시세] 침구: 정산 9건, 정가 대비 중간값 74%", "[price-index] ...
median paid 74% of list"). They are useful (what sells, at what share of list) but anyone can post, and a shop has a
reason to post high prices and a buyer low ones. So the numbers we act on come from the ledger, which every settle
is written to and nobody can edit; the forum's claims are read and checked against it:
  - `categories`: per category (the catalog's last segment), the settles of the last 6 and 24 hours and the price
    paid as a share of list price (median, quartiles);
  - `skus`: the same per SKU with at least two settles;
  - `forum`: each price claim found in a post (a category named and a percentage near it), the ledger's own number
    for that category, and how far apart they are; per author, the mean gap. An author whose claims sit far from the
    ledger is noted for us only: we never accuse anyone on the forum without proof, and the ledger is proof enough
    that our own prices do not follow theirs.
restock_r3.py writes this file every run (the kit runs it every 60 s); the seller's model, the meta-harness and the
report read it. Reading 12 MB of ledger per haggling turn would stall the seller, so it reads this small file.
"""
import argparse
import json
import re
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PCT = re.compile(r"(\d{2,3}(?:\.\d)?)\s*%")
PRICE_WORDS = re.compile(r"시세|price|median|중앙|중간값|index|평균|정가 대비|of list", re.I)
NEAR = 80           # characters after a category name in which its percentage must appear


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def leaf(category):
    return (category or "").split(">")[-1].strip()


def stats(xs):
    xs = sorted(xs)
    q = lambda f: round(xs[min(len(xs) - 1, int(f * len(xs)))], 3)
    return {"n": len(xs), "median": round(statistics.median(xs), 3), "p25": q(0.25), "p75": q(0.75)}


def index(entries, cat, now=None):
    """Per category and per SKU: settles in the last 6 and 24 hours, price paid as a share of list."""
    now = now or time.time()
    by_cat, by_sku = {}, {}
    for e in entries:
        if e.get("kind") != "settle" or now - e.get("ts", 0) > 24 * 3600:
            continue
        d = e.get("data") or {}
        c = cat.get(d.get("item")) or {}
        if not c.get("list_price") or not isinstance(d.get("amount"), (int, float)):
            continue
        share, recent = d["amount"] / c["list_price"], now - e["ts"] <= 6 * 3600
        # per grade too ("sku|A", "leaf|C"): the market pays a median 0.70 of list for A goods, 0.62 B, 0.515 C, so a
        # line over all grades priced our A under its market and our cost (11/10 audit #3: A cap 0.648, cost 0.676)
        g = d.get("grade")
        keys = [(leaf(c.get("category")), by_cat), (d["item"], by_sku)]
        if g in ("A", "B", "C"):
            keys += [(f"{leaf(c.get('category'))}|{g}", by_cat), (f"{d['item']}|{g}", by_sku)]
        for key, out in keys:
            o = out.setdefault(key, {"h24": [], "h6": []})
            o["h24"].append(share)
            if recent:
                o["h6"].append(share)
    cats = {k: {"h6": stats(v["h6"]) if v["h6"] else None, "h24": stats(v["h24"])} for k, v in by_cat.items()}
    skus = {k: stats(v["h24"]) for k, v in by_sku.items() if len(v["h24"]) >= 2}
    return cats, skus


def forum_claims(posts, cats, names, now=None, hours=24):
    """Price claims in forum posts (a category we know named, a percentage within NEAR characters after it), each
    with the ledger's median for that category and the gap in percentage points."""
    now = now or time.time()
    out = []
    for p in posts if isinstance(posts, list) else []:
        text = str(p.get("text") or "")[:3000]
        if now - p.get("ts", 0) > hours * 3600 or not PRICE_WORDS.search(text):
            continue
        for name in names:
            i = text.find(name)
            if i < 0 or text[i + len(name):i + len(name) + 1].isalnum() or (i and text[i - 1].isalnum()):
                continue                                 # a whole word only: 스프 is not 스프링
            m = PCT.search(text, i + len(name), i + len(name) + NEAR)
            if not m:
                continue
            claim = float(m.group(1)) / 100
            if not 0.2 <= claim <= 1.5:
                continue
            truth = (cats.get(name) or {}).get("h24") or {}
            out.append({"id": p.get("id"), "author": p.get("author"), "role": p.get("role"), "category": name,
                        "claim": round(claim, 3), "ledger": truth.get("median"), "ledger_n": truth.get("n"),
                        "gap": round(claim - truth["median"], 3) if truth.get("median") is not None else None})
    return out


def authors(claims):
    """Per author: claims checked against at least 3 ledger settles, and the mean absolute gap."""
    by = {}
    for c in claims:
        if c["gap"] is not None and (c["ledger_n"] or 0) >= 3:
            by.setdefault(c["author"], []).append(abs(c["gap"]))
    return {a: {"claims": len(g), "mean_gap": round(sum(g) / len(g), 3)} for a, g in by.items()}


def build(md, entries=None):
    md = Path(md)
    cat = read_json(md / "catalog.json", {})
    if entries is None:
        entries = read_json(md / "ledger-recent.json", [])
    cats, skus = index(entries, cat)
    names = sorted({leaf(c.get("category")) for c in cat.values() if leaf(c.get("category"))}, key=len, reverse=True)
    claims = forum_claims(read_json(md / "forum.json", []), cats, names)
    return {"t": round(time.time()), "categories": cats, "skus": skus, "forum": claims[-40:], "authors": authors(claims)}


def write(md, entries=None):
    out = build(md, entries)
    tmp = Path(md) / "prices_r3.json.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(Path(md) / "prices_r3.json")
    return out


CACHE = {"mtime": None, "data": {}}


def read(md):
    """The last prices_r3.json, reread only when it changed."""
    p = Path(md) / "prices_r3.json"
    try:
        m = p.stat().st_mtime
    except OSError:
        return {}
    if m != CACHE["mtime"]:
        CACHE.update(mtime=m, data=read_json(p, {}))
    return CACHE["data"]


def median_share(md, sku, category):
    """The 24 h median paid share of list for this SKU, else its category; None without settles."""
    d = read(md)
    s = (d.get("skus") or {}).get(sku) or ((d.get("categories") or {}).get(leaf(category)) or {}).get("h24")
    return s.get("median") if s else None


def line(md, sku, category):
    """One line for the seller's model: what this SKU and its category sold for lately, and what the forum says."""
    d = read(md)
    if not d:
        return "no market data yet"
    parts = []
    s = (d.get("skus") or {}).get(sku)
    if s:
        parts.append(f"this SKU settled {s['n']} times in 24 h at {s['median']:.0%} of list (median)")
    c = (d.get("categories") or {}).get(leaf(category))
    if c:
        h = c.get("h6") or c["h24"]
        parts.append(f"its category {leaf(category)}: {h['n']} settles, median {h['median']:.0%} of list "
                     f"(middle half {h['p25']:.0%}-{h['p75']:.0%})")
    said = [x["claim"] for x in d.get("forum") or [] if x["category"] == leaf(category)]
    if said:
        parts.append(f"forum posts claim {statistics.median(said):.0%} (unchecked; the ledger above is the record)")
    return "; ".join(parts) or "no settles of this SKU or its category in 24 h"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(HERE / ".market"))
    out = write(ap.parse_args().state)
    top = sorted(out["categories"].items(), key=lambda kv: -kv[1]["h24"]["n"])[:10]
    print(json.dumps({"top_categories": dict(top), "authors": out["authors"], "forum_claims": out["forum"][-10:]},
                     ensure_ascii=False, indent=1))
