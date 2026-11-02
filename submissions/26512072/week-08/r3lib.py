"""Shared tools for the Round 3 shop and buyer: the catalog, what the market has been selling (from the public
ledger), and the agents' own memory files. Plain code, no model calls.

Files (all in this folder; .market/ is written by the kit):
    .market/catalog.json      SKU -> name, category, list_price, kind, shelf_hours
    .market/inventory.json    my shop's balance and units
    sales_memory.jsonl        my shop's own sales (seller_r3.py appends)
    buyer_memory_r3.jsonl     my buyer's own trades (market_buyer_r3.py and review_r3.py append)
    market_sales.json         cache of the market's settled deals per SKU (restock.py refreshes)
"""
import json
import os
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
MD = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
SHOP_NAME = os.environ.get("SHOP_NAME", "bjeon01")
SALES = Path(os.environ.get("SHOP_SALES", HERE / "sales_memory.jsonl"))
BUYER_MEM = Path(os.environ.get("BUYER_MEMORY_R3", HERE / "buyer_memory_r3.jsonl"))
MARKET_SALES = HERE / "market_sales.json"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def append_jsonl(path, row):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def catalog():
    return read_json(MD / "catalog.json", {}) or {}


def inventory():
    return read_json(MD / "inventory.json", {}) or {}


def round_now():
    return int(inventory().get("round") or 0)


def hours_left(unit):
    return (unit["expires_at"] - time.time()) / 3600 if unit.get("expires_at") else None


def refresh_market_sales(max_pages=20):
    """Settled deals from the public ledger, accumulated per SKU: count, prices, last time. Incremental:
    remembers the last ledger seq it read. Round 3 'settle' entries name the SKU and the amount paid."""
    cache = read_json(MARKET_SALES, {}) or {}
    seq = cache.get("seq")
    per = cache.get("skus", {})
    if seq is None:
        seq = -500 * max_pages                      # first run: look back this far
    for _ in range(max_pages):
        try:
            rows = httpx.get(f"{MARKET}/api/ledger", params={"after": seq}, timeout=20).json()
        except (httpx.HTTPError, ValueError):
            break
        if not rows:
            break
        for e in rows:
            d = e.get("data") or {}
            if e.get("kind") == "settle" and d.get("item"):
                p = per.setdefault(d["item"], {"n": 0, "amounts": [], "last": 0, "grades": {}})
                p["n"] += 1
                p["amounts"] = (p["amounts"] + [d.get("amount")])[-20:]
                p["last"] = e.get("ts", 0)
                g = d.get("grade")
                if g:
                    p["grades"][g] = p["grades"].get(g, 0) + 1
        seq = rows[-1]["seq"]
        if len(rows) < 500:
            break
    cache = {"seq": seq, "updated": time.time(), "skus": per}
    tmp = MARKET_SALES.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, MARKET_SALES)
    return per


# ---------------------------------------------------------------- forum (read side)
import re as _re

WARN = _re.compile(r"거짓|허위|사기|속|불일치|고장|부패|상했|과다|바가지|lie|lied|false|fake|scam|cheat|overcharg|broke|spoil|mismatch|avoid",
                   _re.I)
GOOD = _re.compile(r"정직|사실 확인|일치|추천|만족|honest|verified|true|matched|recommend|fair", _re.I)
WANT = _re.compile(r"구합니다|구해요|구함|찾습니다|찾아요|삽니다|원해요|wanted|looking for|\bWTB\b|need", _re.I)


def forum_posts():
    posts = read_json(MD / "forum.json", []) or []
    return posts if isinstance(posts, list) else []


def forum_about(name, posts=None):
    """What the forum says about one shop: posts that name it, how many warn and how many praise, net votes."""
    if not name:
        return {"posts": 0, "warn": 0, "good": 0, "score": 0, "quotes": []}
    hits = [p for p in (posts if posts is not None else forum_posts())
            if name.lower() in str(p.get("text", "")).lower() and p.get("author") != SHOP_NAME]
    warn = [p for p in hits if WARN.search(str(p.get("text", "")))]
    good = [p for p in hits if GOOD.search(str(p.get("text", ""))) and p not in warn]
    return {"posts": len(hits), "warn": len(warn), "good": len(good),
            "score": sum(int(p.get("score") or 0) for p in hits),
            "quotes": [str(p.get("text", ""))[:90] for p in (warn or hits)[:2]]}


def forum_wants(cat, posts=None):
    """Categories other agents ask for on the forum ("구합니다", "looking for"): category -> number of posts."""
    leaves = {}
    for x in cat.values():
        leaf = (x.get("category") or "").split(">")[-1]
        for word in _re.split(r"[/ ]", leaf):
            if len(word) >= 2:
                leaves.setdefault(word, set()).add(x["category"])
    out = {}
    for p in (posts if posts is not None else forum_posts()):
        text = str(p.get("text", ""))
        if not WANT.search(text):
            continue
        for word, cats in leaves.items():
            if word in text:
                for c in cats:
                    out[c] = out.get(c, 0) + 1
    return out
