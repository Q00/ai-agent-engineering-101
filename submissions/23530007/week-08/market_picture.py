"""Market picture: the market's flow in numbers, for the memory curator's briefings (user, 11-06).

    from market_picture import picture, profile_buyer, profile_shop
    p = picture()                    # what sells, what buyers buy, the top shops and buyers, ours (JSON-able)
    profile_buyer("lee-sungsoo")     # one buyer in depth; profile_shop("Kiro729") one shop
    python market_picture.py [--buyer NAME | --shop NAME]      prints the same as JSON
    race_view(); funnel_view(); funnel_between(t0, t1)          # 11-13: score race, shop funnel, targets
    python market_picture.py --race | --funnel [--out DIR]      rebuilds and prints them (DIR: writes go there)

The user wants the curator to show the whole market's flow (what sells, what buyers buy, what the rankers
score on) so that each strategist can say how we should move and ask for what it still needs. This module
is the code half of that: it measures, the model interprets. No model call, no text.

Data: .market/shop_value.json (buyer_value.py: every settle of the last 6 h market-wide, broken units, shop
names), .market/catalog.json (list prices, kinds), the public board (GET /api/board?round=3: shop and buyer
scores) and the shops list (GET /api/shops: stock per SKU; the kit's .market/shops.json mirror carries no
stock, so the API is read once per call and the mirror only maps urls to names), market_intel.jsonl (the
orders our buyer received, tail) and seller_r3_log.jsonl (what buyers asked our shop for and we lacked, tail).
shop_value keeps 6 h only, so this module keeps its own hourly buckets (settles, value, sum of price/list per
category and per SKU) in .market/market_picture.json for 48 h: "trend" compares the last 6 h with the 6 h
before. Every source is wrapped: a missing one leaves its section as {"error": ...}; nothing raises.
"""
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".market" / "shop_value.json"
CATALOG = HERE / ".market" / "catalog.json"
SHOPS_FILE = HERE / ".market" / "shops.json"
NAMES_FILE = HERE / ".market" / "shop_names.json"
CACHE = HERE / ".market" / "market_picture.json"
INTEL = HERE / "market_intel.jsonl"
SELLER_LOG = HERE / "seller_r3_log.jsonl"
MARKET = "https://market.ourolabs.ai"
OURS = "yms979"
WINDOW_H = 6                 # what shop_value.json keeps
KEEP_H = 48                  # hourly buckets kept in CACHE
DAY = 24 * 3600
INTEL_LINES = 30000          # tail of market_intel.jsonl read (about 6 MB)
SELLER_BYTES = 3_000_000     # tail of seller_r3_log.jsonl read
SOURCES_TTL = 120            # s: picture() and the profiles in one process share one gather (one GET each)
MAX_BYTES = 12000            # picture() JSON budget; lists shrink until it fits
CAP = {"by_category": 15, "top_skus": 15, "orders": 15, "asked": 10, "top_shops": 8, "top_buyers": 8, "peers": 4,
       "name": 24}


# --- sources -------------------------------------------------------------------------------------------------

def _read_json(path, retries=3):
    """A JSON file another process may be replacing right now."""
    err = None
    for _ in range(retries):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            err = e
            time.sleep(0.2)
    raise err


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _tail(path, max_bytes, max_lines=None):
    """The last lines of a JSONL file (the cut first line dropped), parsed; bad lines skipped."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]
    if max_lines:
        lines = lines[-max_lines:]
    out = []
    for line in lines:
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _shop_name(raw, names):
    s = str(raw or "?").rstrip("/")
    if not s.startswith("http"):
        return s
    return names.get(s) or s.split("//", 1)[-1].split("/", 1)[0]     # a stale tunnel url nobody maps: its host


def _rows(st, cat, names):
    """shop_value rows normalized: shop name, kind, value (amt, or x * list when the sku is known), saved."""
    broken = st.get("broken") or {}
    out = []
    for r in st.get("deals") or []:
        try:
            sku = r.get("sku")
            item = cat.get(sku) if sku else None
            x = float(r["x"])
            if r.get("amt") is not None:
                value = float(r["amt"])
            elif item and item.get("list_price"):
                value = x * item["list_price"]
            else:
                value = None
            unit = r.get("unit")
            broke = unit in broken
            refunded = bool(broken.get(unit)) if broke else False
            c = r.get("cat") or (item or {}).get("category") or ""
            out.append({"ts": float(r["ts"]), "shop": _shop_name(r.get("shop"), names), "buyer": r.get("buyer") or "?",
                        "kind": "goods" if r.get("goods") else "food", "cat": c, "cat_last": c.split(">")[-1] or "?",
                        "x": x, "w": bool(r.get("w")), "grade": r.get("grade") or "?", "sku": sku, "value": value,
                        "saved": (0.0 if refunded else -1.0) if broke else 1.0 - x,   # 11-11: a refunded break scores 0
                        "broke": broke, "refunded": refunded})
        except (KeyError, TypeError, ValueError):
            continue
    return out


_SRC = {"t": 0.0, "src": None}


def _sources(now):
    if _SRC["src"] is not None and 0 <= now - _SRC["t"] < SOURCES_TTL:
        return _SRC["src"]
    src = {"errors": {}}
    try:
        src["st"] = _read_json(STATE)
    except Exception as e:
        src["st"] = None
        src["errors"]["shop_value"] = f"{STATE.name}: {e}"
    try:
        src["cat"] = _read_json(CATALOG)
    except Exception as e:
        src["cat"] = {}
        src["errors"]["catalog"] = f"{CATALOG.name}: {e}"
    try:
        src["board"] = _get(f"{MARKET}/api/board?round=3")
    except Exception as e:
        src["board"] = None
        src["errors"]["board"] = f"board: {e}"
    try:
        sh = _get(f"{MARKET}/api/shops")
        src["shops"] = sh.get("shops") if isinstance(sh, dict) else sh
    except Exception as e:
        src["errors"]["shops"] = f"shops api: {e}"
        try:
            src["shops"] = _read_json(SHOPS_FILE)
        except Exception as e2:
            src["shops"] = None
            src["errors"]["shops"] += f"; {SHOPS_FILE.name}: {e2}"
    names = {}
    for s in src["shops"] or []:
        for url in (s.get("url"), (s.get("card") or {}).get("url")):
            if url:
                names[str(url).rstrip("/")] = s.get("name")
    for s in (src["board"] or {}).get("shops") or []:
        url = (s.get("card") or {}).get("url")
        if url:
            names[str(url).rstrip("/")] = s.get("name")
    try:
        names.update(_read_json(NAMES_FILE).get("names") or {})
    except Exception:
        pass
    names.update((src["st"] or {}).get("names") or {})
    src["names"] = names
    src["rows"] = _rows(src["st"], src["cat"], names) if src["st"] else None
    try:
        src["intel"] = _tail(INTEL, 8_000_000, INTEL_LINES)
    except Exception as e:
        src["intel"] = None
        src["errors"]["intel"] = f"{INTEL.name}: {e}"
    try:
        src["seller"] = _tail(SELLER_LOG, SELLER_BYTES)
    except Exception as e:
        src["seller"] = None
        src["errors"]["seller"] = f"{SELLER_LOG.name}: {e}"
    _SRC.update(t=now, src=src)
    return src


# --- hourly buckets (trend) ----------------------------------------------------------------------------------

def _settles(bucket):
    return sum(e[0] for e in bucket.get("cat", {}).values())


def _buckets(rows, t_data, now):
    """Rebuild the hour buckets inside the 6 h window from the rows, keep older ones, drop those older than
    KEEP_H. Returns settles per category and per sku in the full hours before the window, and how many of
    those WINDOW_H hours the cache has."""
    try:
        hours = _read_json(CACHE).get("hours") or {}
    except Exception:
        hours = {}
    fresh = {}
    for r in rows:
        b = fresh.setdefault(str(int(r["ts"] // 3600) * 3600), {"cat": {}, "sku": {}})
        for key, k in (("cat", r["cat"]), ("sku", r["sku"])):
            if k:
                e = b[key].setdefault(k, [0, 0.0, 0.0])
                e[0] += 1
                e[1] += r["value"] or 0.0
                e[2] += r["x"]
    cut = t_data - WINDOW_H * 3600
    edge = str(int(cut // 3600) * 3600)
    for h, b in fresh.items():
        old = hours.get(h)
        if h == edge:
            # the rows cover only the end of this hour; a bucket built when it was whole stays
            if old and not old.get("part") and _settles(old) >= _settles(b):
                continue
            if cut > int(h):
                b["part"] = True
        hours[h] = b
    keep = now - KEEP_H * 3600
    hours = {h: b for h, b in hours.items() if int(h) >= keep}
    try:
        CACHE.parent.mkdir(exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"t": now, "hours": hours}), encoding="utf-8")
        tmp.replace(CACHE)
    except OSError:
        pass
    prev_cat, prev_sku, n = defaultdict(int), defaultdict(int), 0
    for k in range(1, WINDOW_H + 1):
        b = hours.get(str(int(edge) - k * 3600))
        if not b or b.get("part"):
            continue
        n += 1
        for c, e in b["cat"].items():
            prev_cat[c] += e[0]
        for s, e in b["sku"].items():
            prev_sku[s] += e[0]
    return prev_cat, prev_sku, n


def _prev6(prev, n_hours):
    """Settles in the 6 h before the window, scaled up when the cache knows fewer than 6 of those hours."""
    return prev * WINDOW_H / n_hours if n_hours else None


def _trend(cur, prev6):
    if prev6 is None:
        return None
    if prev6 <= 0:
        return "new" if cur > 0 else None
    return f"{(cur - prev6) / prev6 * 100:+.0f}%"


# --- small helpers -------------------------------------------------------------------------------------------

def _p50(xs):
    return round(statistics.median(xs), 3) if xs else None


def _share(n, d, nd=2):
    return round(n / d, nd) if d else None


def _mean(xs, nd=3):
    return round(sum(xs) / len(xs), nd) if xs else None


def _quartiles(xs):
    if not xs:
        return None
    if len(xs) < 2:
        return [round(xs[0], 3)] * 3
    return [round(v, 3) for v in statistics.quantiles(xs, n=4, method="inclusive")]


def _value(rs):
    vs = [r["value"] for r in rs if r["value"] is not None]
    return round(sum(vs)) if vs else None


def _top(values, k):
    return [[v, n] for v, n in Counter(values).most_common(k)]


def _short(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


def _by(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def _board_index(board):
    shops = {s.get("name"): s for s in (board or {}).get("shops") or []}
    buyers = {b.get("name"): b for b in (board or {}).get("buyers") or []}
    students = {"shop": {}, "buyer": {}}
    for p in (board or {}).get("players") or []:
        for part in ("shop", "buyer"):
            n = (p.get(part) or {}).get("name")
            if n:
                students[part][n] = bool(p.get("student"))
    return shops, buyers, students


def _stock(shops):
    """Per shop {sku: units} and per sku [shops with stock, units]; empty when the source has no stock field."""
    by_shop, by_sku = {}, defaultdict(lambda: [0, 0])
    for s in shops or []:
        st = s.get("stock")
        if not isinstance(st, dict):
            continue
        units = {k: int(v) for k, v in st.items() if isinstance(v, (int, float)) and v > 0}
        by_shop[s.get("name")] = units
        for k, v in units.items():
            by_sku[k][0] += 1
            by_sku[k][1] += v
    return by_shop, by_sku


# --- items -------------------------------------------------------------------------------------------------

def _shop_item(name, rs, board_shop, student, stock):
    b = board_shop or {}
    goods = [r for r in rs if r["kind"] == "goods"]
    item = {"name": _short(name, CAP["name"]), "student": student, "all": b.get("all"), "book": b.get("book"),
            "revenue": b.get("revenue"), "stars": b.get("stars"), "deals_board": b.get("deals"),
            "deals_6h": len(rs), "value_6h": _value(rs), "p50_x": _p50([r["x"] for r in rs]),
            "warranty_share": _share(sum(r["w"] for r in goods), len(goods)),
            "buyers_6h": len({r["buyer"] for r in rs}),
            "top_categories": _top([r["cat_last"] for r in rs], 3),
            "top_skus": _top([r["sku"] for r in rs if r["sku"]], 3),
            "stock_units": sum(stock.values()) if stock is not None else b.get("stock_units"),
            "stock_skus": len(stock) if stock is not None else None}
    if name == OURS:
        item["ours"] = True
    return item


def _buyer_item(name, rs, board_buyer, student):
    b = board_buyer or {}
    goods = [r for r in rs if r["kind"] == "goods"]
    by_shop = sorted(_by(rs, "shop").items(), key=lambda kv: -len(kv[1]))[:3]
    item = {"name": _short(name, CAP["name"]), "student": student, "all": b.get("all"), "saved": b.get("saved"),
            "deals_board": b.get("deals"), "posts": b.get("posts"), "forum": b.get("forum"),
            "deals_6h": len(rs), "deals_per_h": round(len(rs) / WINDOW_H, 1),
            "saved_per_deal_6h": _mean([r["saved"] for r in rs]),
            "food_share": _share(sum(r["kind"] == "food" for r in rs), len(rs)),
            "warranty_share": _share(sum(r["w"] for r in goods), len(goods)),
            "top_shops": [[_short(s, CAP["name"]), len(v), _p50([r["x"] for r in v])] for s, v in by_shop],
            "top_categories": _top([r["cat_last"] for r in rs], 3),
            "breaks_6h": sum(r["broke"] for r in rs), "refunded_6h": sum(r["refunded"] for r in rs)}
    if name == OURS:
        item["ours"] = True
    return item


# --- sections ------------------------------------------------------------------------------------------------

def _what_sells(rows, cat, prev_cat, prev_sku, n_prev, by_sku_stock):
    out = []
    for c, rs in _by(rows, "cat").items():
        prev6 = _prev6(prev_cat.get(c, 0), n_prev)
        out.append({"category": c.split(">")[-1] or "?", "kind": Counter(r["kind"] for r in rs).most_common(1)[0][0],
                    "settles_6h": len(rs), "value_6h": _value(rs), "p50_x": _p50([r["x"] for r in rs]),
                    "settles_prev_6h": round(prev6) if prev6 is not None else None, "trend": _trend(len(rs), prev6)})
    out.sort(key=lambda d: (-(d["value_6h"] or 0), -d["settles_6h"]))
    skus = []
    for s, rs in _by([r for r in rows if r["sku"]], "sku").items():
        item = cat.get(s) or {}
        skus.append({"sku": s, "name": _short(item.get("name"), 30), "category": item.get("category", "").split(">")[-1],
                     "settles_6h": len(rs), "value_6h": _value(rs), "p50_x": _p50([r["x"] for r in rs]),
                     "shops_with_stock": by_sku_stock[s][0] if s in by_sku_stock else None,
                     "units_in_stock": by_sku_stock[s][1] if s in by_sku_stock else None,
                     "trend": _trend(len(rs), _prev6(prev_sku.get(s, 0), n_prev))})
    skus.sort(key=lambda d: -d["settles_6h"])
    return {"by_category": out[:CAP["by_category"]], "top_skus": skus[:CAP["top_skus"]]}


def _orders_seen(intel, now):
    cut = now - DAY
    groups = defaultdict(list)
    for o in intel or []:
        if o.get("type") == "order" and (o.get("t") or 0) >= cut:
            groups[o.get("category") or "?"].append(o)
    out = []
    for c, os_ in groups.items():
        budgets = [o["budget"] for o in os_ if isinstance(o.get("budget"), (int, float))]
        out.append({"category": c.split(">")[-1], "kind": Counter(o.get("kind") for o in os_).most_common(1)[0][0],
                    "orders": len(os_), "budget_median": round(statistics.median(budgets)) if budgets else None,
                    "served_share": _share(sum(bool(o.get("served")) for o in os_), len(os_))})
    out.sort(key=lambda d: -d["orders"])
    return out[:CAP["orders"]]


def _asked_at_our_shop(seller, cat, now):
    """no_stock events of the last 24 h by category: the sku when the line names one, else the longest catalog
    category name found in the buyer's text ("I want to buy a 곡물")."""
    cut = now - DAY
    events = [e for e in seller or [] if (e.get("t") or 0) >= cut]
    segs = {}
    for item in cat.values():
        c = item.get("category") or ""
        if c:
            segs[c.split(">")[-1]] = (c, item.get("kind"))
    groups = defaultdict(lambda: {"n": 0, "skus": Counter(), "kind": None})
    for e in events:
        if e.get("event") != "no_stock":
            continue
        skus = [s for s in e.get("skus") or [] if s]
        full, kind = None, None
        for s in skus:
            if s in cat:
                full, kind = cat[s].get("category"), cat[s].get("kind")
                break
        if not full:
            text = e.get("text") or ""
            hit = max((seg for seg in segs if seg in text), key=len, default=None)
            if hit:
                full, kind = segs[hit]
        g = groups[full or "(unresolved)"]
        g["n"] += 1
        g["kind"] = kind
        g["skus"].update(skus[:1])
    out = [{"category": c.split(">")[-1], "kind": g["kind"], "no_stock": g["n"],
            "skus": [s for s, _ in g["skus"].most_common(3)]} for c, g in groups.items()]
    out.sort(key=lambda d: -d["no_stock"])
    counts = Counter(e.get("event") for e in events)
    return {"asks_24h": counts.get("ask", 0), "deals_24h": counts.get("deal", 0),
            "no_stock_events_24h": counts.get("no_stock", 0), "by_category": out[:CAP["asked"]]}


def _what_buyers_buy(rows, intel, seller, cat, now):
    food = [r for r in rows if r["kind"] == "food"]
    goods = [r for r in rows if r["kind"] == "goods"]
    n = len(rows)
    out = {"kind_mix_6h": {"food": _share(len(food), n), "goods": _share(len(goods), n)},
           "warranty_share_goods_6h": _share(sum(r["w"] for r in goods), len(goods), 3),
           "grade_mix_6h": {g: _share(k, n) for g, k in sorted(Counter(r["grade"] for r in rows).items())},
           "x_quartiles_6h": {"food": _quartiles([r["x"] for r in food]), "goods": _quartiles([r["x"] for r in goods])}}
    try:
        out["orders_seen_by_our_buyer_24h"] = _orders_seen(intel, now) if intel is not None else {"error": "no intel"}
    except Exception as e:
        out["orders_seen_by_our_buyer_24h"] = {"error": str(e)}
    try:
        out["asked_at_our_shop_24h"] = _asked_at_our_shop(seller, cat, now) if seller is not None else {"error": "no log"}
    except Exception as e:
        out["asked_at_our_shop_24h"] = {"error": str(e)}
    return out


def _top_shops(rows, board_shops, students, by_shop_stock):
    rs_by = _by(rows or [], "shop")
    ranked = sorted((s for s in board_shops.values() if s.get("all") is not None), key=lambda s: -s["all"])
    names = [s["name"] for s in ranked[:CAP["top_shops"]]]
    if OURS not in names:
        names.append(OURS)
    return [_shop_item(n, rs_by.get(n, []), board_shops.get(n), students["shop"].get(n, False), by_shop_stock.get(n))
            for n in names]


def _top_buyers(rows, board_buyers, students):
    rs_by = _by(rows or [], "buyer")
    ranked = sorted((b for b in board_buyers.values() if b.get("all") is not None), key=lambda b: -b["all"])
    names = [b["name"] for b in ranked[:CAP["top_buyers"]]]
    if OURS not in names:
        names.append(OURS)
    ours = board_buyers.get(OURS) or {}
    peers = []
    if ours.get("saved") is not None and ours.get("all") is not None:
        # the nearest above us with a saved like ours: what else do they score on?
        peers = sorted((b for b in ranked if b["name"] not in names and b["all"] > ours["all"]
                        and abs((b.get("saved") or 0) - ours["saved"]) <= 0.02), key=lambda b: b["all"])[:CAP["peers"]]
    out = [_buyer_item(n, rs_by.get(n, []), board_buyers.get(n), students["buyer"].get(n, False)) for n in names]
    for b in peers:
        out.append(dict(_buyer_item(b["name"], rs_by.get(b["name"], []), b, students["buyer"].get(b["name"], False)),
                        peer=True))
    return out


def _cut_ranked(lst, n, peers):
    """The first n ranked entries, ours, then at most `peers` peers."""
    ranked = [x for x in lst if not x.get("ours") and not x.get("peer")][:n]
    return ranked + [x for x in lst if x.get("ours")] + [x for x in lst if x.get("peer")][:peers]


def _fit(pic):
    """Shrink the picture until its JSON is within MAX_BYTES: SKU, order and per-item detail first, the
    ranked shops and buyers (never below 6) last. At the full caps the picture runs about 18 KB."""
    def size():
        return len(json.dumps(pic, ensure_ascii=False))

    def cut(section, key, n):
        return lambda: pic[section].__setitem__(key, pic[section][key][:n])

    def cut_items(section, key, n):
        return lambda: [x.__setitem__(key, x[key][:n]) for x in pic[section]]

    def sku_names(n):
        return lambda: [x.__setitem__("name", _short(x["name"], n)) for x in pic["what_sells"]["top_skus"]]

    steps = [cut("what_sells", "top_skus", 10), cut("what_buyers_buy", "orders_seen_by_our_buyer_24h", 10),
             cut("what_sells", "by_category", 12),
             lambda: pic["what_buyers_buy"]["asked_at_our_shop_24h"].__setitem__(
                 "by_category", pic["what_buyers_buy"]["asked_at_our_shop_24h"]["by_category"][:6]),
             cut_items("top_buyers", "top_shops", 2), cut_items("top_buyers", "top_categories", 2),
             cut_items("top_shops", "top_skus", 2), cut_items("top_shops", "top_categories", 2),
             sku_names(20), cut("what_sells", "top_skus", 8), cut("what_sells", "by_category", 10),
             lambda: pic.__setitem__("top_buyers", _cut_ranked(pic["top_buyers"], 8, 3)),
             lambda: pic.__setitem__("top_buyers", _cut_ranked(pic["top_buyers"], 7, 3)),
             lambda: pic.__setitem__("top_shops", _cut_ranked(pic["top_shops"], 7, 0)),
             lambda: pic.__setitem__("top_buyers", _cut_ranked(pic["top_buyers"], 7, 2)),
             lambda: pic.__setitem__("top_buyers", _cut_ranked(pic["top_buyers"], 6, 2)),
             lambda: pic.__setitem__("top_shops", _cut_ranked(pic["top_shops"], 6, 0)),
             cut("what_sells", "top_skus", 6), cut("what_buyers_buy", "orders_seen_by_our_buyer_24h", 8)]
    for step in steps:
        if size() <= MAX_BYTES:
            break
        try:
            step()
        except (KeyError, TypeError, AttributeError):
            continue
    return pic


# --- interface -----------------------------------------------------------------------------------------------

def picture(now=None):
    """The compact picture: what sells, what buyers buy, the top shops and buyers, ours. Never raises."""
    now = time.time() if now is None else float(now)
    try:
        src = _sources(now)
    except Exception as e:
        return {"error": f"sources: {e}"}
    err = src["errors"]
    st, rows, cat = src["st"], src["rows"], src["cat"]
    t_data = (st or {}).get("t")
    pic = {"measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)), "window_h": WINDOW_H,
           "settles_6h": len(rows) if rows is not None else None,
           "data_age_min": round((now - t_data) / 60, 1) if t_data else None}
    by_shop_stock, by_sku_stock = _stock(src["shops"])
    board_shops, board_buyers, students = _board_index(src["board"])
    try:
        if rows is None:
            raise RuntimeError(err.get("shop_value", "no rows"))
        prev_cat, prev_sku, n_prev = _buckets(rows, t_data or now, now)
        pic["what_sells"] = _what_sells(rows, cat, prev_cat, prev_sku, n_prev, by_sku_stock)
    except Exception as e:
        pic["what_sells"] = {"error": str(e)}
    try:
        if rows is None:
            raise RuntimeError(err.get("shop_value", "no rows"))
        pic["what_buyers_buy"] = _what_buyers_buy(rows, src["intel"], src["seller"], cat, now)
    except Exception as e:
        pic["what_buyers_buy"] = {"error": str(e)}
    try:
        if src["board"] is None:
            raise RuntimeError(err.get("board", "no board"))
        pic["top_shops"] = _top_shops(rows, board_shops, students, by_shop_stock)
    except Exception as e:
        pic["top_shops"] = {"error": str(e)}
    try:
        if src["board"] is None:
            raise RuntimeError(err.get("board", "no board"))
        pic["top_buyers"] = _top_buyers(rows, board_buyers, students)
    except Exception as e:
        pic["top_buyers"] = {"error": str(e)}
    pic["ours"] = {}
    for part, lst in (("shop", pic["top_shops"]), ("buyer", pic["top_buyers"])):
        mine = [x for x in lst if x.get("ours")] if isinstance(lst, list) else []
        pic["ours"][part] = mine[0] if mine else {"error": f"{OURS} not on the board"}
    if err:
        pic["errors"] = err
    try:
        return _fit(pic)
    except Exception:
        return pic


def profile_buyer(name, now=None):
    """One buyer in depth: the top_buyers item plus by_shop, by_category, by_hour_6h, kinds, grades."""
    now = time.time() if now is None else float(now)
    try:
        src = _sources(now)
        _, board_buyers, students = _board_index(src["board"])
        rs = [r for r in src["rows"] or [] if r["buyer"] == name]
        if name not in board_buyers and not rs:
            return {"error": f"unknown buyer {name}"}
        out = _buyer_item(name, rs, board_buyers.get(name), students["buyer"].get(name, False))
        out["name"] = name
        by_shop = []
        for s, v in _by(rs, "shop").items():
            goods = [r for r in v if r["kind"] == "goods"]
            by_shop.append({"shop": s, "n": len(v), "p50_x": _p50([r["x"] for r in v]),
                            "saved_per_deal": _mean([r["saved"] for r in v]),
                            "warranty_share": _share(sum(r["w"] for r in goods), len(goods))})
        by_cat = [{"category": c.split(">")[-1], "kind": v[0]["kind"], "n": len(v), "p50_x": _p50([r["x"] for r in v]),
                   "saved_per_deal": _mean([r["saved"] for r in v])} for c, v in _by(rs, "cat").items()]
        hours = Counter(int(r["ts"] // 3600) for r in rs)
        out.update({"by_shop": sorted(by_shop, key=lambda d: -d["n"])[:10],
                    "by_category": sorted(by_cat, key=lambda d: -d["n"])[:10],
                    "by_hour_6h": [[time.strftime("%H:00", time.gmtime(h * 3600)), n] for h, n in sorted(hours.items())],
                    "kinds": {k: _share(n, len(rs)) for k, n in sorted(Counter(r["kind"] for r in rs).items())},
                    "grades": {g: _share(n, len(rs)) for g, n in sorted(Counter(r["grade"] for r in rs).items())}})
        if src["errors"]:
            out["errors"] = src["errors"]
        return out
    except Exception as e:
        return {"error": f"profile_buyer {name}: {e}"}


def profile_shop(name, now=None):
    """One shop in depth: the top_shops item plus by_category, by_sku, stock, buyers."""
    now = time.time() if now is None else float(now)
    try:
        src = _sources(now)
        cat = src["cat"]
        board_shops, _, students = _board_index(src["board"])
        by_shop_stock, _ = _stock(src["shops"])
        rs = [r for r in src["rows"] or [] if r["shop"] == name]
        stock = by_shop_stock.get(name)
        if name not in board_shops and not rs and stock is None:
            return {"error": f"unknown shop {name}"}
        out = _shop_item(name, rs, board_shops.get(name), students["shop"].get(name, False), stock)
        out["name"] = name
        by_cat = [{"category": c.split(">")[-1], "kind": v[0]["kind"], "settles": len(v),
                   "p50_x": _p50([r["x"] for r in v]), "value": _value(v)} for c, v in _by(rs, "cat").items()]
        by_sku = [{"sku": s, "name": _short((cat.get(s) or {}).get("name"), 30), "settles": len(v),
                   "p50_x": _p50([r["x"] for r in v]), "list_price": (cat.get(s) or {}).get("list_price")}
                  for s, v in _by([r for r in rs if r["sku"]], "sku").items()]
        inv = [{"sku": s, "name": _short((cat.get(s) or {}).get("name"), 30), "units": u,
                "list_price": (cat.get(s) or {}).get("list_price")} for s, u in (stock or {}).items()]
        inv.sort(key=lambda d: -(d["units"] * (d["list_price"] or 0)))
        out.update({"by_category": sorted(by_cat, key=lambda d: -d["settles"])[:10],
                    "by_sku": sorted(by_sku, key=lambda d: -d["settles"])[:10], "stock": inv[:15],
                    "buyers": [{"buyer": b, "n": n} for b, n in Counter(r["buyer"] for r in rs).most_common(8)]})
        if src["errors"]:
            out["errors"] = src["errors"]
        return out
    except Exception as e:
        return {"error": f"profile_shop {name}: {e}"}


# --- race, funnel, targets (11-13) ---------------------------------------------------------------------------
# 11-13 (user: 'use everything, highest class total at the round end'): the 17-agent diagnosis found every part
# moving at hour-scale slopes no tool measured with an error bar (book lead 241 M closing -6.7 M/h, revenue 2nd
# held by 2,616 M closing ~30 M/h, saved +.0035/6 h vs seochanit +.0045) and a seller funnel nobody counted end
# to end (847 asks/h -> 67 deals/h, refunds ~.9 M/h). race_view() fits each part from the archived board,
# funnel_view() counts ask -> deal -> paid -> refund for our shop, targets_view() says what each part needs.
# Read-only on every source; the only writes are race.json, funnel.json and funnel_state.json (or --out DIR).

END = 1794927540                                   # 2026-11-17 14:59 UTC, the round end
ARCHIVE = HERE / "data" / "market_archive.db"
MARKET_DIR = HERE / ".market"
INVENTORY = MARKET_DIR / "inventory.json"
BUYER_SCORE = MARKET_DIR / "buyer_score.json"
OUR_URL = "https://shop.bluespot.team"
PTS = {"book": .5 / 45, "revenue": .3 / 45, "stars_rank": .2 / 45, "saved": .5 / 45, "deals": .1 / 45,
       "posts": .1 / 45}                           # total points one rank is worth (percentile over 46 students)
SHOP_PARTS = ("book", "revenue", "stars_rank")
BUYER_PARTS = ("saved", "deals", "posts", "forum")
FIT_HOURS = (3, 6, 12)
RACE_HOURS = 12.5                                  # board history read (~50 snapshots, one per ~15 min)
BUCKET = 600                                       # s: seller-log event buckets
FUNNEL_KEEP_H = 14                                 # buckets and deal units kept in funnel_state.json
ASK_KEEP_H = 2                                     # asks kept to tell a category ask from a SKU ask
SELLER_FIRST_BYTES = 40_000_000                    # first read of the seller log: ~70 h at 11-13 rates
LEDGER_HOURS = 12
FUNNEL_WINDOWS = (1, 3, 12)
SELLER_EVENTS = {"ask": "asks", "no_stock": "no_stock", "deal": "deals", "canceled": "canceled",
                 "lapsed": "lapsed", "lost": "lost", "star_buyer": "star"}
SKU_RE = re.compile(r"G[0-9a-f]{8}")


def _archive():
    return sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True, timeout=30)


def _write_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _fresh(path, max_age, now):
    """The cached JSON when younger than max_age seconds, else None."""
    try:
        if max_age and max_age > 0 and now - Path(path).stat().st_mtime < max_age:
            return _read_json(path)
    except Exception:
        pass
    return None


def _num(x, nd=6):
    if x is None:
        return None
    return round(x) if abs(x) >= 1000 else round(x, nd)


def _ols(pts, now, hours):
    """Slope per hour and its standard error over the last `hours` (x = hours from now); None under 4 points."""
    pts = [(t, v) for t, v in pts if t >= now - hours * 3600 and v is not None]
    n = len(pts)
    if n < 4:
        return None
    xs = [(t - now) / 3600 for t, _ in pts]
    ys = [float(v) for _, v in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    ssr = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    return {"slope": b, "se": max(1e-9, math.sqrt(ssr / (n - 2) / sxx)), "n": n}


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _hours_left(now):
    return max(0.0, (END - now) / 3600)


# --- race --------------------------------------------------------------------------------------------------------

def _board_series(now):
    """series[player][part] = [(ts, value)] for the students over RACE_HOURS, the latest snapshot
    {player: {part: value, shop, buyer}} and its ts. Player = the GitHub login (p['player'])."""
    with closing(_archive()) as c:
        rows = c.execute("SELECT ts, data FROM board WHERE ts > ? ORDER BY ts", (now - RACE_HOURS * 3600,)).fetchall()
    series = defaultdict(lambda: defaultdict(list))
    latest, latest_ts = None, None
    for ts, raw in rows:
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            continue
        shops = {s.get("name"): s for s in d.get("shops") or []}
        buyers = {b.get("name"): b for b in d.get("buyers") or []}
        snap = {}
        for p in d.get("players") or []:
            if not p.get("student"):
                continue
            name = p.get("player") or (p.get("owner") or {}).get("login")
            sn, bn = (p.get("shop") or {}).get("name"), (p.get("buyer") or {}).get("name")
            s, b = shops.get(sn) or {}, buyers.get(bn) or {}
            vals = {k: s.get(k) for k in SHOP_PARTS}
            vals.update({k: b.get(k) for k in BUYER_PARTS})
            vals["total"] = p.get("student_total")
            for k, v in vals.items():
                if isinstance(v, (int, float)):
                    series[name][k].append((ts, float(v)))
            snap[name] = dict(vals, shop=sn, buyer=bn)
        if snap:
            latest, latest_ts = snap, ts
    return series, latest, latest_ts, len(rows)


def _fits(pts, now):
    return {h: _ols(pts, now, h) for h in FIT_HOURS}


def _slopes(fits):
    out = {}
    for h, f in fits.items():
        out[f"slope{h}"] = _num(f["slope"]) if f else None
        out[f"se{h}"] = _num(f["se"]) if f else None
    return out


def _s(fits, h=6):
    return fits[h]["slope"] if fits.get(h) else None


def _neighbour(name, value, ours, us_fits, n_fits, side, H, part, snap, runner_up, h=6):
    """One student next to us on a part: gap, slopes and the chance the order flips by the round end
    (normal on the 6 h fits: mu = gap - closing x H, sd = SE x H plus 2% of the gap for model error)."""
    gap = abs(ours - value)
    nb = {"name": name, "as": snap.get(name, {}).get("shop" if part in SHOP_PARTS else "buyer"),
          "value": _num(value), "gap": _num(gap), **_slopes(n_fits), "vs_runner_up": name == runner_up}
    su, sn = _s(us_fits, h), _s(n_fits, h)
    nb["proj_end"] = _num(value + sn * H) if sn is not None else None
    if su is None or sn is None or not us_fits.get(h) or not n_fits.get(h):
        nb.update(dslope=None, mu=None, sd=None, p_flip=None, eta_h=None)
        return nb
    dslope = (su - sn) if side == "above" else (sn - su)          # > 0: the gap closes
    mu = gap - dslope * H
    sd = math.sqrt(us_fits[h]["se"] ** 2 + n_fits[h]["se"] ** 2) * H + 0.02 * gap
    p = _phi(-mu / sd) if sd > 0 else (1.0 if mu < 0 else 0.0)
    nb.update(dslope=_num(dslope), mu=_num(mu), sd=_num(sd), p_flip=round(p, 3),
              eta_h=round(gap / dslope, 1) if dslope > 0 else None)
    return nb


def _saved_mean_end(name, snap, series, now, H):
    """End saved of a running mean (saved = sum v / (deals + 100)): sum and deals each go on at their 6 h slopes.
    A straight line on saved itself overshoots over days (the mean flattens as deals grow): at 92 h it said .7816
    for us against this .7661 and buyer_score's m_star .7664. Returns (end, marginal v per deal, deals/h) or None."""
    try:
        sv, dv = snap[name].get("saved"), snap[name].get("deals")
        d = dict(series[name]["deals"])
        pts = [(t, v * (d[t] + 100)) for t, v in series[name]["saved"] if t in d]
        fS, fD = _ols(pts, now, 6), _ols(series[name]["deals"], now, 6)
        if None in (sv, dv, fS, fD) or fD["slope"] <= 0:
            return None
        end = (sv * (dv + 100) + fS["slope"] * H) / (dv + 100 + fD["slope"] * H)
        return round(end, 5), round(fS["slope"] / fD["slope"], 4), round(fD["slope"], 1)
    except Exception:
        return None


def _part_view(part, snap, series, now, H, runner_up):
    vals = {n: v[part] for n, v in snap.items() if isinstance(v.get(part), (int, float))}
    if OURS not in vals:
        return {"error": f"{OURS} has no {part}"}
    ours = vals[OURS]
    above = [(n, v) for n, v in vals.items() if n != OURS and v > ours]
    below = [(n, v) for n, v in vals.items() if n != OURS and v <= ours]
    out = {"ours": _num(ours), "rank": 1 + len(above), "of": len(vals),
           "owner": "shop" if part in SHOP_PARTS else "buyer"}
    if part == "forum":
        return out
    us_fits = _fits(series[OURS][part], now)
    out.update(_slopes(us_fits))
    s6 = _s(us_fits)
    out["proj_end"] = _num(ours + s6 * H) if s6 is not None else None
    out["pts_per_rank"] = round(PTS[part], 5)
    for side, lst, pick in (("above", above, min), ("below", below, max)):
        if lst:
            n, v = pick(lst, key=lambda kv: kv[1])
            out[side] = _neighbour(n, v, ours, us_fits, _fits(series[n][part], now), side, H, part, snap, runner_up)
            # 11-14 (user: 'do you act on the warnings?'): the shop agent read book as decided (P(flip) .002 against
            # SungJinho, the student right behind) while inextro, 3rd, closed 2.5-3.6 M/h and projected to pass by
            # the end on the 24 h fit. The neighbour on each side is now the student among the 6 nearest with the
            # HIGHEST P(flip), and the adjacent one stays as '<side>_adjacent'
            near = sorted(lst, key=lambda kv: (ours - kv[1]) if side == "below" else (kv[1] - ours))[:6]
            best = out[side]
            for n2, v2 in near[1:]:
                try:
                    f2 = _fits(series[n2][part], now)
                    # a non-adjacent student counts on the MORE conservative of its 6 h and 12 h fits: seochanit's
                    # revenue read +154 M/h on 6 h and -6 M/h on 12 h (a board jump), P(flip) .65 from a 6,490 M gap
                    cs = [c_ for c_ in (_neighbour(n2, v2, ours, us_fits, f2, side, H, part, snap, runner_up, hh)
                                        for hh in (6, 12)) if c_.get("p_flip") is not None]
                    if len(cs) < 2:
                        continue
                    c = min(cs, key=lambda c_: c_["p_flip"])
                except Exception:   # noqa: BLE001
                    continue
                if (c.get("p_flip") or 0) > (best.get("p_flip") or 0):
                    best = c
            if best is not out[side]:
                out[f"{side}_adjacent"] = out[side]
                out[side] = best
        else:
            out[side] = None
    if part == "saved":
        for name, tgt in ((OURS, out), *(((out[k] or {}).get("name"), out[k]) for k in ("above", "below") if out[k])):
            m = _saved_mean_end(name, snap, series, now, H)
            if m:
                tgt.update(proj_end_mean=m[0], v_marg6=m[1], deals_h6=m[2])
    return out


def race_view(max_age=600, out_dir=None, now=None):
    """Every scored part: our value, rank among the students, the students just above and below with slopes
    (3/6/12 h OLS on the archived board), projected end values and the chance each order flips; the total
    race with the runner-up; targets (targets_view). Cached in out_dir/race.json for max_age seconds."""
    out_dir = Path(out_dir) if out_dir else MARKET_DIR
    now = time.time() if now is None else float(now)
    cached = _fresh(out_dir / "race.json", max_age, now)
    if cached is not None:
        return cached
    t_start = time.time()
    try:
        series, snap, snap_ts, n_rows = _board_series(now)
        if not snap or OURS not in snap:
            return {"error": f"no board snapshot with {OURS} in the last {RACE_HOURS} h ({n_rows} rows)"}
        H = _hours_left(now)
        others = {n: v.get("total") for n, v in snap.items() if n != OURS and isinstance(v.get("total"), (int, float))}
        runner_up = max(others, key=others.get) if others else None
        race = {"t": now, "measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)),
                "board_ts": snap_ts, "board_age_min": round((now - snap_ts) / 60, 1), "board_rows": n_rows,
                "hours_left": round(H, 2), "students": len(snap), "runner_up": runner_up, "parts": {}}
        for part in ("book", "revenue", "stars_rank", "saved", "deals", "posts", "forum"):
            try:
                race["parts"][part] = _part_view(part, snap, series, now, H, runner_up)
            except Exception as e:
                race["parts"][part] = {"error": f"{part}: {e}"}
        ours_t = snap[OURS].get("total")
        tot = {"ours": ours_t, "runner_up": others.get(runner_up),
               "gap": _num(ours_t - others[runner_up]) if runner_up and ours_t is not None else None}
        if runner_up:
            theirs = dict(series[runner_up]["total"])
            gap_pts = [(t, v - theirs[t]) for t, v in series[OURS]["total"] if t in theirs]
            for h in (6, 12):
                f = _ols(gap_pts, now, h)
                tot[f"gap_slope{h}"] = _num(f["slope"]) if f else None
            # what the targets need of the runner-up's own parts (book slope, revenue projection)
            race["runner_up_parts"] = {p: {"value": _num(snap[runner_up].get(p)), **_slopes(_fits(series[runner_up][p], now))}
                                       for p in ("book", "revenue", "stars_rank", "saved")}
        race["total"] = tot
        try:
            race["targets"] = targets_view(race, funnel_view(out_dir=out_dir, now=now))
        except Exception as e:
            race["targets"] = {"error": f"targets: {e}"}
        race["build_s"] = round(time.time() - t_start, 2)
        _write_atomic(out_dir / "race.json", race)
        return race
    except Exception as e:
        return {"error": f"race_view: {e}"}


# --- funnel ------------------------------------------------------------------------------------------------------

def _load_state(out_dir):
    try:
        st = _read_json(out_dir / "funnel_state.json", retries=1)
        if isinstance(st, dict):
            st.setdefault("b", {})
            st.setdefault("units", {})
            st.setdefault("asks", {})
            return st
    except Exception:
        pass
    return {"offset": None, "b": {}, "units": {}, "asks": {}}


def _read_seller(st, now):
    """Bucket the seller-log events written since the last read (the first read: the last ~40 MB)."""
    size = SELLER_LOG.stat().st_size
    off = st.get("offset")
    with open(SELLER_LOG, "rb") as f:
        if not isinstance(off, int) or off > size:          # first run, or the log shrank: rebuild
            start = max(0, size - SELLER_FIRST_BYTES)
            f.seek(start)
            if start:
                f.readline()
            st.update(b={}, units={}, asks={})
        else:
            f.seek(off)
        pos = f.tell()
        data = f.read(max(0, size - pos))
    end = data.rfind(b"\n")
    if end < 0:
        st["offset"] = pos
        st["read_t"] = now
        return
    st["offset"] = pos + end + 1                            # a half-written last line is read next time
    cut = now - FUNNEL_KEEP_H * 3600
    b, units, asks = st["b"], st["units"], st["asks"]
    for line in data[:end].split(b"\n"):
        if not line or b'"event": "msg"' in line[:60]:      # chat lines: 60% of the log, not counted
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t, ev = e.get("t"), e.get("event")
        key = SELLER_EVENTS.get(ev)
        if key is None or not isinstance(t, (int, float)) or t < cut:
            continue
        bk = b.setdefault(str(int(t // BUCKET)), {})
        bk[key] = bk.get(key, 0) + 1
        if ev == "ask" and e.get("task"):
            asks[e["task"]] = [t, SKU_RE.findall(e.get("text") or "")]
        elif ev == "deal" and e.get("unit"):
            a = asks.get(e.get("task"))
            units[e["unit"]] = [t, e.get("sku"), e.get("grade"), e.get("cost"), e.get("price"),
                                (e.get("sku") not in a[1]) if a else None]
    st["read_t"] = now
    kcut = int(cut // BUCKET)
    st["b"] = {k: v for k, v in b.items() if int(k) >= kcut}
    st["units"] = {u: v for u, v in units.items() if v[0] >= cut}
    st["asks"] = {k: v for k, v in asks.items() if v[0] >= now - ASK_KEEP_H * 3600}


def _bucket_counts(st, t0, t1):
    """Seller-log events in [t0, t1] from the 10-min buckets; a bucket cut by the window counts pro rata
    (the bucket being written covers only up to the last read)."""
    read_t = st.get("read_t") or t1
    tot = Counter()
    for k, cnt in st["b"].items():
        s = int(k) * BUCKET
        e = min(s + BUCKET, read_t)
        span = e - s
        ov = min(e, t1) - max(s, t0)
        if span <= 0 or ov <= 0:
            continue
        f = min(1.0, ov / span)
        for kk, v in cnt.items():
            tot[kk] += v * f
    return tot


def _our_urls(c, st, now):
    u = st.get("our_urls")
    if u and now - st.get("our_urls_t", 0) < 3600:
        return set(u)
    urls = {OUR_URL}
    for (raw,) in c.execute("SELECT data FROM ledger WHERE kind = 'join' AND data LIKE ?", (f'%"{OURS}"%',)):
        try:
            d = json.loads(raw).get("data") or {}
        except (TypeError, ValueError):
            continue
        if d.get("name") == OURS and d.get("url"):
            urls.add(str(d["url"]))
    st["our_urls"], st["our_urls_t"] = sorted(urls), now
    return urls


def _ledger_ours(c, urls, t0, t1):
    """Our shop's ledger rows in (t0, t1]: paid settles, refunds, expiries, storage fees, supply lines."""
    urls = sorted(urls) + [u + "/" for u in urls]
    q = ",".join("?" * len(urls))
    L = {"paid": [], "refunds": [], "expired": [], "storage": [], "supply": []}
    for ts, raw in c.execute(f"SELECT ts, data FROM ledger WHERE kind = 'settle' AND shop IN ({q}) AND ts > ? AND ts <= ?",
                             (*urls, t0, t1)):
        d = (json.loads(raw).get("data") or {})
        L["paid"].append({"ts": ts, "unit": d.get("unit"), "buyer": d.get("buyer"), "sku": d.get("sku") or d.get("item"),
                          "grade": d.get("grade"), "amount": float(d.get("amount") or 0),
                          "warranty_hours": d.get("warranty_hours")})
    for ts, raw in c.execute(f"SELECT ts, data FROM ledger WHERE kind = 'broken' AND shop IN ({q}) AND ts > ? AND ts <= ?",
                             (*urls, t0, t1)):
        d = (json.loads(raw).get("data") or {})
        L["refunds"].append({"ts": ts, "unit": d.get("unit"), "refunded": float(d.get("refunded") or 0),
                             "hours_after_sale": d.get("hours_after_sale")})
    names = [OURS] + urls
    qn = ",".join("?" * len(names))
    for kind, key in (("expire", "expired"), ("storage", "storage"), ("supply", "supply")):
        for ts, raw in c.execute(f"SELECT ts, data FROM ledger WHERE kind = ? AND shop IN ({qn}) AND ts > ? AND ts <= ?",
                                 (kind, *names, t0, t1)):
            d = (json.loads(raw).get("data") or {})
            if kind != "supply" and d.get("shop") != OURS:
                continue
            L[key].append({"ts": ts, "cost": float(d.get("cost") or 0), "fee": float(d.get("fee") or 0),
                           "total": float(d.get("total") or 0), "sku": d.get("sku")})
    return L


def _rivals(now):
    """Buyer names of the 5 students with the highest student_total other than us (latest board)."""
    with closing(_archive()) as c:
        row = c.execute("SELECT data FROM board WHERE ts > ? ORDER BY ts DESC LIMIT 1", (now - 6 * 3600,)).fetchone()
    if not row:
        return []
    ps = [p for p in json.loads(row[0]).get("players") or [] if p.get("student") and p.get("player") != OURS
          and isinstance(p.get("student_total"), (int, float))]
    ps.sort(key=lambda p: -p["student_total"])
    return [[p.get("player"), (p.get("buyer") or {}).get("name")] for p in ps[:5]]


def _paid_stats(paid, units, cat, rivals):
    out = {"paid_n": len(paid), "paid_goods_n": 0, "paid_food_n": 0, "paid_c_goods_n": 0, "paid_rev": 0.0,
           "paid_margin": 0.0, "margin_unknown_n": 0, "under_cost_paid_n": 0}
    by_grade = {g: [0, 0.0] for g in "ABC"}
    riv = {bn: {"player": pl, "n": 0, "v": []} for pl, bn in rivals if bn}
    for p in paid:
        item = cat.get(p["sku"]) or {}
        goods = item.get("kind") == "goods"
        out["paid_goods_n"] += goods
        out["paid_food_n"] += item.get("kind") == "food"
        out["paid_c_goods_n"] += goods and p["grade"] == "C"
        out["paid_rev"] += p["amount"]
        u = units.get(p["unit"])
        cost = u[3] if u and isinstance(u[3], (int, float)) else None
        if cost is None:
            out["margin_unknown_n"] += 1
        else:
            m = p["amount"] - cost
            out["paid_margin"] += m
            out["under_cost_paid_n"] += p["amount"] < cost
            if p["grade"] in by_grade:
                by_grade[p["grade"]][0] += 1
                by_grade[p["grade"]][1] += m
        r = riv.get(p["buyer"])
        if r is not None and goods:
            r["n"] += 1
            lp = item.get("list_price")
            if cost is not None and lp and lp > cost:
                r["v"].append((lp - p["amount"]) / (lp - cost))
    out["margin_by_grade"] = {g: [n, round(s)] for g, (n, s) in by_grade.items()}
    feed = {bn: {"player": r["player"], "n": r["n"], "share": _share(r["n"], out["paid_goods_n"], 3),
                 "median_v": _p50(r["v"])} for bn, r in riv.items()}
    out["rival_feed"] = {"buyers": feed, "share_sum": round(sum(r["n"] for r in riv.values()) / out["paid_goods_n"], 3)
                         if out["paid_goods_n"] else 0.0}
    return out


def _window(L, st, cat, rivals, t0, t1):
    h = max(1e-9, (t1 - t0) / 3600)
    sel = lambda rows: [r for r in rows if t0 < r["ts"] <= t1]
    bc = _bucket_counts(st, t0, t1)
    w = {"asks": bc.get("asks", 0), "no_stock": bc.get("no_stock", 0), "deals_agreed": bc.get("deals", 0),
         "canceled": bc.get("canceled", 0), "lapsed": bc.get("lapsed", 0), "lost": bc.get("lost", 0),
         "star": bc.get("star", 0)}
    w.update(_paid_stats(sel(L["paid"]), st["units"], cat, rivals))
    ref = sel(L["refunds"])
    w["refunds_n"] = sum(r["refunded"] > 0 for r in ref)
    w["breaks_n"] = len(ref)
    w["refunds_amt"] = sum(r["refunded"] for r in ref)
    w["expired_cost"] = sum(r["cost"] for r in sel(L["expired"]))
    w["storage_fee"] = sum(r["fee"] for r in sel(L["storage"]))
    sup = sel(L["supply"])
    w["supply_lines"], w["supply_cost"] = len(sup), sum(r["total"] for r in sup)
    rates = {f"{k}_h": round(v / h, 3 if abs(v / h) < 1000 else 0) for k, v in w.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
    for k, v in list(w.items()):
        if isinstance(v, float):
            w[k] = round(v, 1) if abs(v) < 1000 else round(v)
    w.update(rates)
    return w


def _ratings(now, t0=None, t1=None):
    try:
        rows = _read_json(STATE).get("our_ratings") or []
    except Exception:
        return []
    if t0 is not None:
        rows = [r for r in rows if t0 < r[0] <= t1]
    return rows


def _funnel_build(st, now, t0, t1, rivals=None):
    """Shared by funnel_view and funnel_between: reads the new seller-log lines and our ledger rows."""
    _read_seller(st, now)
    with closing(_archive()) as c:
        urls = _our_urls(c, st, now)
        L = _ledger_ours(c, urls, t0, t1)
    try:
        cat = _read_json(CATALOG)
    except Exception:
        cat = {}
    return L, cat, (rivals if rivals is not None else _rivals(now))


def funnel_view(max_age=600, out_dir=None, now=None):
    """Our shop's funnel over 1, 3 and 12 h: asks, none-in-stock, deals agreed, canceled, lapsed (seller log);
    paid, margin, refunds, expiries, storage, supply (archived ledger); rival students' buyers' share of our
    paid goods; the stars of our newest 50 reviews; cash and stock. Cached in out_dir/funnel.json."""
    out_dir = Path(out_dir) if out_dir else MARKET_DIR
    now = time.time() if now is None else float(now)
    cached = _fresh(out_dir / "funnel.json", max_age, now)
    if cached is not None:
        return cached
    t_start = time.time()
    try:
        st = _load_state(out_dir)
        L, cat, rivals = _funnel_build(st, now, now - LEDGER_HOURS * 3600, now)
        fn = {"t": now, "measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)),
              "ledger_lag_min": None, "rivals": rivals}
        last = max([r["ts"] for k in L for r in L[k]], default=None)
        fn["ledger_lag_min"] = round((now - last) / 60, 1) if last else None
        for W in FUNNEL_WINDOWS:
            fn[f"w{W}"] = _window(L, st, cat, rivals, now - W * 3600, now)
        paid6 = Counter(p["sku"] for p in L["paid"] if p["ts"] > now - 6 * 3600)
        fn["paid_by_sku_6h"] = dict(paid6.most_common())
        ratings = _ratings(now)[-50:]
        fn["stars_new_mean_50"] = _mean([r[2] for r in ratings], 3)
        fn["stars_new_n"] = len(ratings)
        try:
            inv = _read_json(INVENTORY)
            units = [u for u in inv.get("units") or [] if isinstance(u.get("cost"), (int, float))]
            fn["restock"] = {"cash": inv.get("balance"), "stock_cost": round(sum(u["cost"] for u in units)),
                             "stock_units": len(units), "keep_cash": _keep_cash()}
            fn["goods_skus_in_stock"] = sorted({u.get("sku") for u in units if u.get("kind") == "goods"})
        except Exception as e:
            fn["restock"] = {"error": str(e)}
        fn["build_s"] = round(time.time() - t_start, 2)
        _write_atomic(out_dir / "funnel_state.json", st)
        _write_atomic(out_dir / "funnel.json", fn)
        return fn
    except Exception as e:
        return {"error": f"funnel_view: {e}"}


def _keep_cash():
    """keep_cash as policy.shop_params() merges it, without its side effects (shop_params notes the regime in
    policy_shop.json and logs/policy.log): the regime is read from policy_shop.json."""
    import policy
    cfg = policy.read_json(policy.PARAMS, {})
    regime = (policy.read_json(policy.STATE["shop"], {}) or {}).get("regime") or "STEADY"
    return policy.merged(cfg, "shop", regime).get("keep_cash")


def funnel_between(t0, t1, out_dir=None, now=None):
    """Per-hour rates of our shop in [t0, t1]: paid deals, paid margin, revenue (paid minus refunds), refunds,
    none-in-stock, canceled, and the mean stars of our reviews in the window; raw counts n_paid, n_ratings."""
    out_dir = Path(out_dir) if out_dir else MARKET_DIR
    now = time.time() if now is None else float(now)
    t0, t1 = float(t0), float(t1)
    try:
        st = _load_state(out_dir)
        L, cat, _ = _funnel_build(st, now, t0, t1, rivals=[])
        _write_atomic(out_dir / "funnel_state.json", st)
        w = _window(L, st, cat, [], t0, t1)
        h = max(1e-9, (t1 - t0) / 3600)
        ratings = _ratings(now, t0, t1)
        log_from = min((int(k) * BUCKET for k in st["b"]), default=None)
        covered = log_from is not None and t0 >= log_from          # seller-log buckets and unit costs reach back 14 h
        known = w["paid_n"] - w["margin_unknown_n"]
        return {"t0": t0, "t1": t1, "hours": round(h, 3),
                "paid_deals_h": round(w["paid_n"] / h, 3),
                "paid_margin_h": round(w["paid_margin"] / h) if known > 0 or w["paid_n"] == 0 else None,
                "revenue_h": round((w["paid_rev"] - w["refunds_amt"]) / h), "refunds_h": round(w["refunds_amt"] / h),
                "no_stock_h": round(w["no_stock"] / h, 3) if covered else None,
                "canceled_h": round(w["canceled"] / h, 3) if covered else None,
                "stars_new_mean": _mean([r[2] for r in ratings], 3), "n_paid": w["paid_n"], "n_ratings": len(ratings),
                "margin_unknown_n": w["margin_unknown_n"], "seller_log_from": log_from}
    except Exception as e:
        return {"error": f"funnel_between: {e}"}


# --- targets -----------------------------------------------------------------------------------------------------

def _t(value, need, ok, owner, **extra):
    return {"value": value, "need": need, "status": None if ok is None else ("ON" if ok else "OFF"), "owner": owner,
            **extra}


def _proj(nb, h, H):
    """End value on the h-hour fit; nb is a neighbour ('value') or our part view ('ours')."""
    nb = nb or {}
    s, v = nb.get(f"slope{h}"), nb.get("value", nb.get("ours"))
    return v + s * H if s is not None and v is not None else None


def targets_view(race, funnel):
    """What each part needs by the round end, ON when the current pace meets it. Thresholds 11-13 from the
    diagnosis: book lead >= 50 M at the end, revenue 2nd held by >= 200 M, refunds <= .35 M/h, rival students'
    buyers <= 15% of our paid goods, newest stars >= 4.5, expiry <= 1 M/12 h, buyer v6 >= .845, >= 85 deals/h."""
    H = race.get("hours_left") or 0.0
    Hd = max(H, 1.0)
    P = race.get("parts") or {}
    ru = race.get("runner_up_parts") or {}
    w3, w12 = funnel.get("w3") or {}, funnel.get("w12") or {}
    T = {}

    book = P.get("book") or {}
    below = book.get("below") or {}
    if book.get("ours") is not None and below:
        lead = book["ours"] - below["value"]
        rs6 = (ru.get("book") or {}).get("slope6")
        cand = [s for s in (rs6, below.get("slope6")) if s is not None]
        need = max(cand) - (lead - 50e6) / Hd if cand else None
        v = book.get("slope6")
        T["book_slope"] = _t(v, _num(need), (v >= need) if v is not None and need is not None else None, "shop",
                             lead=_num(lead), below=below.get("name"))
        ends = {}
        for h in (6, 12):
            us = _proj(book, h, H)
            rivals = [x for x in (_proj(below, h, H), _proj(ru.get("book"), h, H)) if x is not None]
            ends[h] = _num(us - max(rivals)) if us is not None and rivals else None
        ok = None if None in ends.values() else all(e >= 50e6 for e in ends.values())
        T["book_projection"] = _t(ends.get(6), 50e6, ok, "shop", lead_end_6h=ends.get(6), lead_end_12h=ends.get(12))

    rev = P.get("revenue") or {}
    rb = rev.get("below") or {}
    if rev.get("ours") is not None and rb:
        gap = rev["ours"] - rb["value"]
        need = rb["slope6"] - (gap - 200e6) / Hd if rb.get("slope6") is not None else None
        v = rev.get("slope6")
        T["revenue_hold"] = _t(v, _num(need), (v >= need) if v is not None and need is not None else None, "shop",
                               gap=_num(gap), below=rb.get("name"))

    if w3:
        r = w3.get("refunds_amt", 0) / 3
        T["refunds"] = _t(round(r), 0.35e6, r <= 0.35e6, "shop")
        share = (w3.get("rival_feed") or {}).get("share_sum", 0.0)
        T["rival_feed"] = _t(share, 0.15, share <= 0.15 or w3.get("paid_goods_n", 0) < 15, "shop",
                             paid_goods_n_3h=w3.get("paid_goods_n"))
    sn = funnel.get("stars_new_mean_50")
    T["stars_new"] = _t(sn, 4.5, (sn >= 4.5) if sn is not None else None, "shop")
    if w12:
        T["food_expiry"] = _t(w12.get("expired_cost"), 1e6, w12.get("expired_cost", 0) <= 1e6, "shop")

    try:
        bs = _read_json(BUYER_SCORE)
    except Exception:
        bs = {}
    v6 = (bs.get("v6") or {}).get("all")
    T["buyer_v6"] = _t(v6, 0.845, (v6 >= 0.845) if v6 is not None else None, "buyer")
    deals = P.get("deals") or {}
    d3 = deals.get("slope3")
    T["buyer_deals_h"] = _t(d3, 85, (d3 >= 85) if d3 is not None else None, "buyer")

    sv = P.get("saved") or {}
    sa = sv.get("above") or {}
    key = "proj_end_mean" if sa.get("proj_end_mean") is not None and sv.get("proj_end_mean") is not None else "proj_end"
    if sa.get(key) is not None and sv.get(key) is not None:
        need = sa[key] + 0.0005
        T["saved_need"] = _t(sv[key], round(need, 5), sv[key] >= need, "buyer", model=key,
                             F_hat=bs.get("m_star"), f_per_rank=round(need - sv[key], 5), above=sa.get("name"),
                             linear_value=sv.get("proj_end"), linear_need=_num(sa["proj_end"] + 0.0005)
                             if sa.get("proj_end") is not None else None)
    margins = []
    if deals.get("proj_end") is not None:
        for side in ("above", "below"):
            nb = deals.get(side) or {}
            if nb.get("proj_end") is not None:
                margins.append(abs(deals["proj_end"] - nb["proj_end"]))
    T["deals_in_play"] = _t(bool(margins) and min(margins) <= 150, 150, None, "buyer",
                            margin_end=_num(min(margins)) if margins else None)
    po = (P.get("posts") or {}).get("slope6")
    T["posts"] = _t(po, 11.95, (po >= 11.95) if po is not None else None, "buyer")

    phase = "normal" if H > 24 else "T-24" if H > 12 else "T-12" if H > 6 else "T-6" if H > 3 else "T-3"
    T["phase"] = _t(phase, None, None, "both", hours_left=round(H, 2))

    rp = {"value": False, "need": None, "status": None, "owner": "shop"}
    if H <= 12 and rev.get("ours") is not None and rb:
        Gs = []
        for h in (3, 6):
            us, th = _proj(rev, h, H), _proj(rb, h, H)
            if us is not None and th is not None:
                Gs.append(us - th)
        G = min(Gs) if Gs else None
        C = 0.5 * w3.get("paid_rev", 0) / 3 * H
        ends = T.get("book_projection") or {}
        bl = [ends.get("lead_end_6h"), ends.get("lead_end_12h")]
        share = (w3.get("rival_feed") or {}).get("share_sum", 1.0)
        a = G is not None and -C < G < 0
        b = None not in bl and all(x > 80e6 for x in bl)
        c = share < 0.15
        rp.update(value=bool(a and b and c), G=_num(G), C=_num(C), book_lead_end=bl, rival_share_3h=share,
                  a=a, b=b, c=c)
    else:
        rp["why"] = "H > 12" if H > 12 else "no revenue neighbour"
    rp["status"] = "ON" if rp["value"] else "OFF"
    T["rev_push_ok"] = rp

    if H <= 24:
        paid6 = funnel.get("paid_by_sku_6h") or {}
        zero = [s for s in funnel.get("goods_skus_in_stock") or [] if paid6.get(s, 0) < 12]
        T["endgame_cover_zero"] = _t(zero, 12, None, "shop", n=len(zero))
    return T


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] in (["--race"], ["--funnel"]):
        out = None
        if "--out" in args:
            i = args.index("--out")
            if i + 1 >= len(args):
                sys.exit(f"usage: {sys.argv[0]} --race | --funnel [--out DIR]")
            out = args[i + 1]
        res = race_view(max_age=0, out_dir=out) if args[0] == "--race" else funnel_view(max_age=0, out_dir=out)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        sys.exit(0)
    if args and args[0] in ("--buyer", "--shop") and len(args) < 2:
        sys.exit(f"usage: {sys.argv[0]} [--buyer NAME | --shop NAME]")
    if args[:1] == ["--buyer"]:
        res = profile_buyer(args[1])
    elif args[:1] == ["--shop"]:
        res = profile_shop(args[1])
    else:
        res = picture()
    print(json.dumps(res, ensure_ascii=False, indent=1))
