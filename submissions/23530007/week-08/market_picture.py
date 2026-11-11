"""Market picture: the market's flow in numbers, for the memory curator's briefings (user, 11-06).

    from market_picture import picture, profile_buyer, profile_shop
    p = picture()                    # what sells, what buyers buy, the top shops and buyers, ours (JSON-able)
    profile_buyer("lee-sungsoo")     # one buyer in depth; profile_shop("Kiro729") one shop
    python market_picture.py [--buyer NAME | --shop NAME]      prints the same as JSON

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
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
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


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("--buyer", "--shop") and len(args) < 2:
        sys.exit(f"usage: {sys.argv[0]} [--buyer NAME | --shop NAME]")
    if args[:1] == ["--buyer"]:
        res = profile_buyer(args[1])
    elif args[:1] == ["--shop"]:
        res = profile_shop(args[1])
    else:
        res = picture()
    print(json.dumps(res, ensure_ascii=False, indent=1))
