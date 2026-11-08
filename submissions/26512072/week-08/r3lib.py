"""Shared tools for the Round 3 shop and buyer: the catalog, what the market has been selling (from the public
ledger), and the agents' own memory files. Plain code, no model calls.

Files (all in this folder; .market/ is written by the kit):
    .market/catalog.json      SKU -> name, category, list_price, kind, shelf_hours
    .market/inventory.json    my shop's balance and units
    sales_memory.jsonl        my shop's own sales (seller_r3.py appends)
    buyer_memory_r3.jsonl     my buyer's own trades (market_buyer_r3.py and review_r3.py append)
    market_sales.json         cache of the market's settled deals per SKU (restock.py refreshes)
"""
import email.utils
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
CLOCK = MD / "clock.json"
R3_START = 2_130_000        # ledger seq where Round 3 opened (11-03 12:01 market time)
FLOW = HERE / "market_flow.json"
FLOW_HOURS = 3


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


# Round 3 end = the midterm PR deadline (the market does not publish it). Stock is not counted in profit, so the
# shop winds down before it: from WIND_DOWN no expensive buying and old stock sells down to 70% of cost; from
# CLOSE_OUT no buying at all and everything sells down to 50% of cost (KST, epoch seconds).
WIND_DOWN = 1794754800      # 2026-11-16 00:00 KST
CLOSE_OUT = 1794884400      # 2026-11-17 12:00 KST


def endgame():
    """0 = normal, 1 = wind down, 2 = close out."""
    now = market_now()
    return 2 if now >= CLOSE_OUT else 1 if now >= WIND_DOWN else 0


def round_now():
    return int(inventory().get("round") or 0)


def market_now():
    """time.time() on the market's clock. This server's clock runs about 10 minutes fast, and supply windows and
    expiry times are market timestamps, so the offset comes from the market's Date header (measured every 30 min)."""
    c = read_json(CLOCK, {}) or {}
    if time.time() - c.get("at", 0) > 1800:
        try:
            t0 = time.time()
            r = httpx.head(f"{MARKET}/agent.md", timeout=5)
            server = email.utils.parsedate_to_datetime(r.headers["date"]).timestamp()
            c = {"at": t0, "skew": round((t0 + time.time()) / 2 - server, 1)}
        except Exception:
            c = {"at": time.time(), "skew": c.get("skew", 0)}     # keep the last offset, try again later
        CLOCK.write_text(json.dumps(c), encoding="utf-8")
    return time.time() - c.get("skew", 0)


def hours_left(unit):
    return (unit["expires_at"] - market_now()) / 3600 if unit.get("expires_at") else None


def refresh_market_sales(max_pages=40):
    """Settled deals from the public ledger, accumulated per SKU: count, prices, last time, and every settle of the
    last FLOW_HOURS ("recent": [ts, amount, grade]). Incremental: remembers the last ledger seq it read, and says
    whether it has reached the end of the ledger ("caught_up"). Returns the per-SKU table."""
    cache = read_json(MARKET_SALES, {}) or {}
    seq = cache.get("seq")
    per = cache.get("skus", {})
    if seq is None or seq < R3_START:               # Round 2 had no settle entries: start where Round 3 began
        seq, per = R3_START, {}
    if cache.get("v") != 2:                         # v1 kept no times per settle: reread about the last 3 hours
        seq, per = max(R3_START, seq - 50_000), {}
    caught_up = False
    for _ in range(max_pages):
        try:
            rows = httpx.get(f"{MARKET}/api/ledger", params={"after": seq}, timeout=20).json()
        except (httpx.HTTPError, ValueError):
            break
        if not rows:
            caught_up = True
            break
        for e in rows:
            d = e.get("data") or {}
            if e.get("kind") == "settle" and d.get("item"):
                p = per.setdefault(d["item"], {"n": 0, "amounts": [], "last": 0, "grades": {}, "recent": []})
                p["n"] += 1
                p["amounts"] = (p["amounts"] + [d.get("amount")])[-20:]
                p["last"] = e.get("ts", 0)
                g = d.get("grade")
                if g:
                    p["grades"][g] = p["grades"].get(g, 0) + 1
                p.setdefault("recent", []).append([e.get("ts", 0), d.get("amount"), g])
        seq = rows[-1]["seq"]
        if len(rows) < 500:
            caught_up = True
            break
    cut = market_now() - FLOW_HOURS * 3600
    for p in per.values():
        p["recent"] = [r for r in p.get("recent", []) if r[0] >= cut]
    cache = {"v": 2, "seq": seq, "updated": time.time(), "caught_up": caught_up, "skus": per}
    tmp = MARKET_SALES.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, MARKET_SALES)
    return per


def market_flow(per, cat):
    """Every FLOW_HOURS, a snapshot of the market's last FLOW_HOURS (from refresh_market_sales): credits settled per
    SKU, and per category (and grade) the median price as a share of the list price. Written to market_flow.json
    for restock.py (what to buy) and seller_r3.py (where to open); each new snapshot is logged to
    logs/market-flow.log. Returns the snapshot in use."""
    flow = read_json(FLOW, {}) or {}
    if time.time() - flow.get("at", 0) < FLOW_HOURS * 3600 or not (read_json(MARKET_SALES, {}) or {}).get("caught_up"):
        return flow
    skus, ratios = {}, {}
    for sku, p in per.items():
        if sku not in cat or not p.get("recent"):
            continue
        lp = cat[sku]["list_price"]
        skus[sku] = {"n": len(p["recent"]), "credits": sum(r[1] or 0 for r in p["recent"])}
        k = cat[sku]["category"]
        for _, amount, g in p["recent"]:
            if amount:
                ratios.setdefault(k, []).append(amount / lp)
                ratios.setdefault(f"{k}|{g}", []).append(amount / lp)
    med = lambda xs: sorted(xs)[len(xs) // 2]
    prices = {k: {"n": len(v), "ratio": round(med(v), 3)} for k, v in ratios.items() if len(v) >= 5}
    flow = {"at": time.time(), "hours": FLOW_HOURS, "skus": skus, "prices": prices}
    tmp = FLOW.with_suffix(".tmp")
    tmp.write_text(json.dumps(flow, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FLOW)
    bycat = {}
    for sku, x in skus.items():
        k = cat[sku]["category"].split(">")[-1]
        bycat[k] = bycat.get(k, 0) + x["credits"]
    total = sum(bycat.values()) or 1
    top = sorted(skus, key=lambda k: -skus[k]["credits"])[:5]
    append_jsonl(HERE / "logs" / "market-flow.log", {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "hours": FLOW_HOURS, "settles": sum(x["n"] for x in skus.values()),
        "credits": total, "top_categories": [[k, round(v / total, 3)] for k, v in sorted(bycat.items(), key=lambda kv: -kv[1])[:6]],
        "top_skus": [[cat[k]["name"][:30], skus[k]["n"], skus[k]["credits"]] for k in top],
        "price_ratio": {k.split(">")[-1]: v["ratio"] for k, v in sorted(prices.items(), key=lambda kv: -kv[1]["n"])
                        if "|" not in k and v["n"] >= 30}})
    return flow
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
