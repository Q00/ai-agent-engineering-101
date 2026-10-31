"""How my stock decisions turned out, so the next decision can learn from them.

decisions.jsonl holds what the restock agent decided and why. The ledger holds what my shop sold, inventory.json what is left,
and the seller's memory what each sold unit cost. A SKU's units are asked for, then either sold, still here, or gone: thrown away
past their date (asked - sold - left). "asked" is what the agent wanted: the market can refuse a lot the balance cannot pay.
"""
import collections
import json
import os
import time

import market_model
import shop_memory

MIN_AGE = 3600     # a decision younger than this has not had time to show anything


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def my_sales(rows, me_shops):
    seller = {r["order"]: r.get("seller") for r in rows if r["k"] == "result" and r.get("order")}
    return [r for r in rows if r["k"] == "settle" and (r.get("shop") in me_shops or seller.get(r.get("order")) in me_shops)]


def unit_costs(path=None):
    """{sku: mean cost} of what my shop agreed to sell, from its Round 3 memory."""
    costs = collections.defaultdict(list)
    try:
        with open(path or shop_memory.MEMORY_R3, encoding="utf-8") as f:
            for n in json.load(f)["negotiations"]:
                if n.get("result") == "deal" and n.get("sku") and n.get("cost"):
                    costs[n["sku"]].append(n["cost"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {k: sum(v) / len(v) for k, v in costs.items()}


def outcomes(md, inv, last=5, top=10, now=None, me_shops=None, costs=None):
    """{"decisions": newest few with their results, "skus": best and worst SKUs across all of them, "totals"}."""
    now = now or time.time()
    me_shops = me_shops if me_shops is not None else market_model.my_shops()
    decisions = [d for d in read_jsonl(os.path.join(md, "decisions.jsonl")) if d.get("agent") == "restock" and (d.get("final") or {}).get("buy")]
    sales = collections.defaultdict(list)
    for s in my_sales(market_model.load_rows(md), me_shops):
        if s.get("sku") and s.get("ts"):
            sales[s["sku"]].append(s)
    costs = unit_costs() if costs is None else costs
    left = collections.Counter(u.get("sku") for u in inv.get("units") or [])
    asked_at = collections.defaultdict(list)
    for d in decisions:
        for b in d["final"]["buy"]:
            asked_at[b["sku"]].append(d["ts"])

    def sold_between(sku, start, end):
        return [s for s in sales.get(sku, []) if start <= s["ts"] < end]

    recent = []
    for d in decisions[-last:]:
        if now - d["ts"] < MIN_AGE:
            continue
        lines = []
        for b in d["final"]["buy"]:
            later = [t for t in asked_at[b["sku"]] if t > d["ts"]]
            sold = sold_between(b["sku"], d["ts"], min(later, default=float("inf")))
            lines.append({"sku": b["sku"], "asked": b["qty"], "sold": len(sold), "revenue": sum(s.get("amount") or 0 for s in sold)})
        recent.append({"hours_ago": round((now - d["ts"]) / 3600, 1), "why": (d.get("final") or {}).get("why"), "lines": lines})

    table = []
    for sku, times in asked_at.items():
        first = min(times)
        asked = sum(b["qty"] for d in decisions for b in d["final"]["buy"] if b["sku"] == sku)
        sold = sold_between(sku, first, float("inf"))
        revenue = sum(s.get("amount") or 0 for s in sold)
        row = {"sku": sku, "asked": asked, "sold": len(sold), "left": left[sku], "gone_unsold": max(0, asked - len(sold) - left[sku]),
               "revenue": revenue, "hours_since_first": round((now - first) / 3600, 1)}
        if sku in costs:
            row["margin"] = round(revenue - costs[sku] * len(sold))
        table.append(row)
    table.sort(key=lambda r: -(r.get("margin") if "margin" in r else r["revenue"]))
    sold_out = [r for r in table if r["sold"]]
    never = sorted((r for r in table if not r["sold"] and r["hours_since_first"] >= MIN_AGE / 3600), key=lambda r: -r["hours_since_first"])
    return {"decisions": recent, "best_skus": sold_out[:top], "never_sold": never[:top],
            "totals": {"decisions": len(decisions), "skus": len(table), "asked": sum(r["asked"] for r in table), "sold": sum(r["sold"] for r in table),
                       "left": sum(r["left"] for r in table), "gone_unsold": sum(r["gone_unsold"] for r in table), "revenue": sum(r["revenue"] for r in table)}}
