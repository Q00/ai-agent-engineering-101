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


def write_json(path, obj):
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path, row):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def catalog():
    return read_json(MD / "catalog.json", {}) or {}


def inventory():
    return read_json(MD / "inventory.json", {}) or {}


# Round 3 end = the midterm PR deadline (the market does not publish it). The score counts unsold stock at cost
# (book), so the shop does not sell goods off below cost; it stops buying at CLOSE_OUT (nothing bought then can sell
# in time), and from WIND_DOWN food that would spoil before ROUND_END sells down to half its cost (KST, epoch s).
WIND_DOWN = 1794754800      # 2026-11-16 00:00 KST
CLOSE_OUT = 1794884400      # 2026-11-17 12:00 KST
ROUND_END = 1794927540      # 2026-11-17 23:59 KST


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


SHOP_PRICES = HERE / "shop_prices.json"
SHOP_KEEP = 100


def refresh_shop_prices(max_pages=40):
    """What each shop settles at, as a share of the list price: the last SHOP_KEEP deals per shop name, from the
    public ledger's result rows (a result with a receipt is a deal; the receipt names the shop and the price). The
    buyer ranks shops by it, since its score (saved) is the share of the list price it did not pay. Incremental like
    refresh_market_sales; the first run starts 100,000 rows back (about 4 hours)."""
    cache = read_json(SHOP_PRICES, {}) or {}
    shops, alias = cache.get("shops", {}), cache.get("alias", {})
    urls = cache.get("urls", {})                    # every tunnel URL a shop joined with (mine change ~30 min)
    mine = cache.get("mine", [])                    # my settles and refunds: [ts, kind, credits, sku]
    seq = cache.get("seq")
    cat = catalog()
    # a receipt names the shop as its seller wrote it ("Nocha Furniture"); the buyer knows the market name
    # ("nocha12"). The row's shop URL is the shop's tunnel, which the board lists per market name.
    try:
        board = httpx.get(f"{MARKET}/api/board", timeout=20).json()["shops"]
        url2name = {(b.get("card") or {}).get("url", "").rstrip("/"): b["name"] for b in board if b.get("card")}
    except (httpx.HTTPError, ValueError, KeyError):
        url2name = {}
    urls.update(url2name)
    if seq is None:
        try:
            tip = httpx.get(f"{MARKET}/api/ledger", params={"after": -1}, timeout=20).json()
            seq = max(R3_START, tip[-1]["seq"] - 100_000)
        except (httpx.HTTPError, ValueError, IndexError, KeyError):
            return shops
    for _ in range(max_pages):
        try:
            rows = httpx.get(f"{MARKET}/api/ledger", params={"after": seq}, timeout=20).json()
        except (httpx.HTTPError, ValueError):
            break
        if not rows:
            break
        for e in rows:
            d, kind = e.get("data") or {}, e.get("kind")
            if kind == "join" and d.get("role") == "shop" and d.get("url"):
                urls[d["url"].rstrip("/")] = d.get("name")
            elif kind in ("settle", "broken") and urls.get(str(d.get("shop") or "").rstrip("/")) == SHOP_NAME:
                mine.append([e.get("ts", 0), kind, d.get("amount") if kind == "settle" else d.get("refunded") or 0,
                             d.get("item")])
            r = d.get("receipt") if kind == "result" else None
            if isinstance(r, dict) and r.get("shop") and r.get("item") in cat and isinstance(r.get("price"), (int, float)):
                url = str(e["data"].get("shop") or "").rstrip("/")
                name = url2name.get(url) or (url.split("/shop/")[-1] if "/shop/" in url else None)
                if name:
                    alias[r["shop"]] = name
                name = name or alias.get(r["shop"])
                if not name:
                    continue
                lst = shops.setdefault(name, [])
                lst.append(round(r["price"] / cat[r["item"]]["list_price"], 3))
                del lst[:-SHOP_KEEP]
        seq = rows[-1]["seq"]
        if len(rows) < 500:
            break
    mine = [m for m in mine if m[0] >= market_now() - 24 * 3600]
    tmp = SHOP_PRICES.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": seq, "updated": time.time(), "shops": shops, "alias": alias, "urls": urls,
                               "mine": mine, "mine_since": cache.get("mine_since") or market_now()}),
                   encoding="utf-8")
    os.replace(tmp, SHOP_PRICES)
    return shops


TUNE = HERE / "tune.json"
TUNE_LOG = HERE / "logs" / "tune.log"
# the range each restock limit may move in; a step is x1.2 (stock) or x1.3 (MAX_LIST) up, x0.8 / x0.75 down
TUNE_BOUNDS = {"STOCK_CAP": (8_000_000, 30_000_000), "CHEAP_CAP": (2_000_000, 8_000_000),
               "MAX_LIST": (300_000, 3_000_000)}


def tune(inv, cat, defaults):
    """Every FLOW_HOURS, moves restock's limits inside TUNE_BOUNDS by what my own shop did (11-12, owner asked for
    code that adjusts the strategy by itself). Reads my settles and refunds from refresh_shop_prices and my stock
    from inventory.json:
      - stock that waited over 24 h is under 15% of all stock (it turns over): more stock (STOCK_CAP x1.2); over
        35%: less (x0.8). The same for cheap SKUs and CHEAP_CAP.
      - MAX_LIST (the dearest SKU it buys): warranty refunds over 12% of my goods revenue in the last 12 h, or over
        30% of the dear stock (list above half of MAX_LIST) unsold after 48 h: x0.75. Refunds under 6% and under
        15% of it that old: x1.3, so dearer goods (sofas: 39% of the market's credits on 11-12) get tried step by
        step. (Dear goods take about a day to sell, and unsold stock still counts at cost, so 48 h, not 24.)
    Waits until the ledger has 3 hours of my own records. Logs every decision to logs/tune.log; returns the values."""
    t = read_json(TUNE, {}) or {}
    vals = {**defaults, **(t.get("values") or {})}
    now = market_now()
    sp = read_json(SHOP_PRICES, {}) or {}
    if now - t.get("at", 0) < FLOW_HOURS * 3600 or now - (sp.get("mine_since") or now) < FLOW_HOURS * 3600:
        return vals
    mine = sp.get("mine") or []
    goods = lambda sku: (cat.get(sku) or {}).get("kind") == "goods"
    rev = sum(m[2] or 0 for m in mine if m[1] == "settle" and goods(m[3]) and m[0] >= now - 12 * 3600)
    refunds = sum(m[2] or 0 for m in mine if m[1] == "broken" and m[0] >= now - 12 * 3600)
    refund_rate = refunds / rev if rev else 0.0
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    old = lambda u: now - (u.get("supplied_at") or now) > 24 * 3600
    share = lambda us: sum(u["cost"] for u in us if old(u)) / max(1, sum(u["cost"] for u in us))
    cheap = [u for u in units if u.get("list_price", 0) <= 100_000]
    dear = [u for u in units if u.get("list_price", 0) > vals["MAX_LIST"] / 2]
    dear_old = sum(u["cost"] for u in dear if now - (u.get("supplied_at") or now) > 48 * 3600) / max(1, sum(
        u["cost"] for u in dear))
    new, why = dict(vals), []
    for key, us in (("STOCK_CAP", units), ("CHEAP_CAP", cheap)):
        sh = share(us)
        if sh < 0.15:
            new[key], w = vals[key] * 1.2, "up"
        elif sh > 0.35:
            new[key], w = vals[key] * 0.8, "down"
        else:
            w = "keep"
        why.append(f"{key} {w}: {sh:.0%} of it waited over 24 h")
    if refund_rate > 0.12 or dear_old > 0.30:
        new["MAX_LIST"], w = vals["MAX_LIST"] * 0.75, "down"
    elif refund_rate < 0.06 and dear_old < 0.15:
        new["MAX_LIST"], w = vals["MAX_LIST"] * 1.3, "up"
    else:
        w = "keep"
    why.append(f"MAX_LIST {w}: refunds {refund_rate:.1%} of goods revenue (12 h), {dear_old:.0%} of the dear stock "
               f"waited over 48 h")
    for k, (lo, hi) in TUNE_BOUNDS.items():
        new[k] = int(min(hi, max(lo, new[k])))
    write_json(TUNE, {"at": now, "values": new})
    append_jsonl(TUNE_LOG, {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "before": {k: vals[k] for k in TUNE_BOUNDS},
                            "after": {k: new[k] for k in TUNE_BOUNDS}, "goods_revenue_12h": rev, "refunds_12h": refunds,
                            "why": why})
    return new


def shop_ratio(name, shops=None):
    """Median price / list price of a shop's recent deals, or None with fewer than 5."""
    lst = (shops if shops is not None else (read_json(SHOP_PRICES, {}) or {}).get("shops", {})).get(name) or []
    return sorted(lst)[len(lst) // 2] if len(lst) >= 5 else None


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
