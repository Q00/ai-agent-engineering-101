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
    """Our shop's settled sales of one SKU."""
    s = _d(_d(_d(_d(summary).get("seller")).get("skus")).get(sku))
    return {"settled": int(s.get("settled") or 0), "last_sale_ts": s.get("last_sale_ts"),
            "sales_per_h": float(s.get("sales_per_h") or 0.0)}


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


def summary_ref(kind, key):
    """A memory_refs entry: shop:<url>, cat:<leaf>, sku:<sku>."""
    kind = {"category": "cat", "shop": "shop", "sku": "sku"}.get(kind, kind)
    if kind == "cat":
        key = _leaf(key)
    elif kind == "shop":
        key = str(key or "").rstrip("/")
    return f"{kind}:{key}"
