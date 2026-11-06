"""Read-only views of memory/summary.json for the seller, the buyer and restock.

The summary is built only by memory/ingest.py (inside the restock hook). Every function here
returns neutral defaults for unknown or missing data and never raises, so a broken or absent
summary turns into "no memory", not a failed hook.
"""
from memory.store import MEM, read_json

SUMMARY = MEM / "summary.json"


def _d(x):
    return x if isinstance(x, dict) else {}


def _leaf(category):
    return str(category or "").split(">")[-1]


def load_summary():
    """memory/summary.json as a dict; {} if missing or broken."""
    s = read_json(SUMMARY, {})
    return s if isinstance(s, dict) else {}


def shop_stats(summary, shop_url):
    """Our buyer's record with one shop (keyed by URL)."""
    s = _d(_d(_d(_d(summary).get("buyer")).get("shops")).get(str(shop_url or "").rstrip("/")))
    return {"attempts": int(s.get("attempts") or 0), "paid": int(s.get("paid") or 0),
            "refused": dict(_d(s.get("refused"))), "bind_mismatch": int(s.get("bind_mismatch") or 0),
            "broken_confirmed": int(s.get("broken_confirmed") or 0),
            "broken_inferred": int(s.get("broken_inferred") or 0)}


def category_stats(summary, category):
    """use_within_hours seen in our orders, budget / median price, and the public goods breakage rate."""
    c = _d(_d(_d(_d(summary).get("buyer")).get("category")).get(_leaf(category)))
    uwh, br, q = _d(c.get("uwh")), _d(c.get("budget_ratio")), _d(c.get("q"))
    return {"uwh": {"n": int(uwh.get("n") or 0), "median": uwh.get("median")},
            "budget_ratio": {"n": int(br.get("n") or 0), "p25": br.get("p25"), "p50": br.get("p50")},
            "q": {"n": int(q.get("n") or 0), "upper": q.get("upper")}}


def sku_stats(summary, sku):
    """Our shop's settled sales of one SKU (sales_per_h over 24 h, sold_6h in the last 6 h)."""
    s = _d(_d(_d(_d(summary).get("seller")).get("skus")).get(sku))
    return {"settled": int(s.get("settled") or 0), "last_sale_ts": s.get("last_sale_ts"),
            "sales_per_h": float(s.get("sales_per_h") or 0.0), "sold_6h": int(s.get("sold_6h") or 0)}


def regular_categories(summary):
    """Category leaves our regular customers buy most (L1)."""
    cats = _d(_d(summary).get("seller")).get("regular_cats")
    return [c for c in cats if isinstance(c, str)] if isinstance(cats, list) else []


def supply_demand(summary):
    """{category leaf: {"demand": float, "supply": int|None}} for the L1 priority."""
    out = {}
    for leaf, v in _d(_d(_d(summary).get("market")).get("categories")).items():
        v = _d(v)
        out[leaf] = {"demand": float(v.get("demand") or 0.0),
                     "supply": v.get("supply") if isinstance(v.get("supply"), int) else None}
    return out


# market-wide views (memory/ingest.render_market): None when the summary has no market data, so callers can tell
# "no data" from "no sales"

C_KIND_FALLBACK = {"food": 0.51, "goods": 0.61}              # our supply cost / list before we have own rows


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _market(summary, key):
    v = _d(_d(summary).get("market")).get(key)
    return v if isinstance(v, dict) else None


def market_rate(summary, sku):
    """Settles per hour of one SKU across all shops over the last 6 h; 0.0 when none, None without data."""
    r = _market(summary, "sku_rate")
    return None if r is None else _num(r.get(sku)) or 0.0


def market_settles_48h(summary, sku):
    """Settles of one SKU across all shops in the last 48 h (see market_covered_h); None without data."""
    r = _market(summary, "sku_settles_48h")
    return None if r is None else int(_num(r.get(sku)) or 0)


def supply_1h(summary, sku):
    """Units of one SKU all shops (us too) bought in the last hour, as of the summary; None without data."""
    r = _market(summary, "supply_1h")
    return None if r is None else int(_num(r.get(sku)) or 0)


def p_clear(summary, kind):
    """Median paid / list price of the market's settles of a kind ('food'|'goods') over 6 h, or None."""
    return _num((_market(summary, "p_clear") or {}).get(kind))


def shop_price(summary, shop_url, sku, leaf, min_sku_n=2, min_cat_n=3):
    """What one shop's settled sales kept of list over the last 24 h, from the public ledger (the price book):
    {"share": median paid/list, "n", "level"} by SKU with at least min_sku_n sales, else by category leaf with
    at least min_cat_n, else None. A settled price is firmer evidence than a first ask."""
    book = _market(summary, "price_book")
    if book is None:
        return None
    s = _d(book.get(str(shop_url or "").rstrip("/")))
    for level, key, need in (("sku", sku, min_sku_n), ("cat", _leaf(leaf), min_cat_n)):
        e = _d(_d(s.get(level)).get(key))
        n, med = int(_num(e.get("n")) or 0), _num(e.get("med"))
        if med is not None and n >= need:
            return {"share": med, "n": n, "level": level}
    return None


def sku_seqs(summary, sku):
    """Newest public settle seqs of one SKU (at most 3), for a post's refs; [] without data."""
    r = _market(summary, "sku_seqs")
    v = r.get(sku) if r else None
    return [x for x in v if type(x) is int] if isinstance(v, list) else []


def sku_price_book(summary, sku):
    """Every shop's settled share of list for one SKU over 24 h: {shop url: {"share", "n"}}."""
    book = _market(summary, "price_book") or {}
    out = {}
    for url, levels in book.items():
        e = _d(_d(_d(levels).get("sku")).get(sku))
        n, med = int(_num(e.get("n")) or 0), _num(e.get("med"))
        if med is not None and n > 0:
            out[str(url)] = {"share": med, "n": n}
    return out


def c_kind(summary, kind):
    """Our mean supply cost / list price of a kind over 24 h, else the fallback."""
    return _num((_market(summary, "c_kind") or {}).get(kind)) or C_KIND_FALLBACK.get(kind)


def market_covered_h(summary):
    """Hours the per-SKU market folds cover at the summary's build time; 0.0 without data."""
    m = _d(_d(summary).get("market"))
    since, built = _num(m.get("since")), _num(_d(summary).get("built_at"))
    return round(max(0.0, built - since) / 3600, 2) if since is not None and built is not None else 0.0


def summary_ref(kind, key):
    """A memory_refs entry: shop:<url>, cat:<leaf>, sku:<sku>."""
    kind = {"category": "cat", "shop": "shop", "sku": "sku"}.get(kind, kind)
    if kind == "cat":
        key = _leaf(key)
    elif kind == "shop":
        key = str(key or "").rstrip("/")
    return f"{kind}:{key}"
