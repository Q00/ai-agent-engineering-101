"""The one tool the analysis models have: ledger_query, read-only over the collected ledger.

What is small and always needed (our own records' summary) goes into the prompt; what is
large and needed in parts (the whole market's settles and breakdowns) is fetched through
this tool. The model chooses what to look at; the numbers are counted by code, so a model
cannot make one up. Limits held by code: read-only, the answer cut to 1,500 characters, and
at most MAX_CALLS calls in one analysis (harness.run_tools).
"""
import json
import time

import common
import ledger

MAX_CALLS = 3
LIMIT = 1500

SCHEMA = {"type": "function", "function": {
    "name": "ledger_query",
    "description": "Counts from the market's public ledger over the last `hours` hours, for one category "
                   "(name as in the catalog, e.g. 김치), one SKU, or one shop: settled deals, average price "
                   "paid as a share of the list price, grades sold, grade claims and how many were true, "
                   "goods that broke after the sale, and the shops that sold the most. Give at most one of "
                   "category, sku or shop; none gives the whole market.",
    "parameters": {"type": "object", "properties": {
        "category": {"type": "string"}, "sku": {"type": "string"}, "shop": {"type": "string"},
        "hours": {"type": "number", "description": "1 to 72, default 24"}},
        "additionalProperties": False}}}


def ledger_query(category: str | None = None, sku: str | None = None, shop: str | None = None,
                 hours: float = 24, now: float | None = None) -> dict:
    now = now or time.time()
    hours = max(1, min(72, float(hours or 24)))
    catalog = common.catalog()
    since = now - hours * 3600
    leaf = lambda s: (catalog.get(s) or {}).get("category", "").split(">")[-1]

    def wanted(row):
        if sku:
            return row.get("item") == sku
        if shop:
            return row.get("shop") == shop
        if category:
            full = (catalog.get(row.get("item")) or {}).get("category", "")
            return category in (full, leaf(row.get("item")))
        return True
    settles = [s for s in common.read_jsonl(ledger.folder() / "settles.jsonl") if (s.get("ts") or 0) >= since and wanted(s)]
    broken = [b for b in common.read_jsonl(ledger.folder() / "broken.jsonl") if (b.get("ts") or 0) >= since and wanted(b)]
    ratios = [s["amount"] / catalog[s["item"]]["list_price"] for s in settles
              if isinstance(s.get("amount"), (int, float)) and (catalog.get(s.get("item")) or {}).get("list_price")]
    grades, shops = {}, {}
    for s in settles:
        grades[s.get("grade")] = grades.get(s.get("grade"), 0) + 1
        if s.get("shop"):
            shops[s["shop"]] = shops.get(s["shop"], 0) + 1
    claims = [s for s in settles if s.get("claim")]
    return {"hours": hours, "of": {"category": category, "sku": sku, "shop": shop}, "settled": len(settles),
            "avg_price_share": round(sum(ratios) / len(ratios), 3) if ratios else None, "grades": grades,
            "claims": len(claims), "claims_true": sum(1 for s in claims if s.get("claim_honest") is True),
            "broken": len(broken), "top_shops": dict(sorted(shops.items(), key=lambda kv: -kv[1])[:5])}


def run(name: str, args: dict) -> str:
    """Execute one tool call from a model; the answer is JSON text, cut to LIMIT."""
    if name != "ledger_query":
        return json.dumps({"error": f"no tool {name}"})
    try:
        out = ledger_query(**{k: v for k, v in (args or {}).items() if k in ("category", "sku", "shop", "hours")})
    except (TypeError, ValueError) as e:
        out = {"error": str(e)}
    return json.dumps(out, ensure_ascii=False)[:LIMIT]
