"""Observed agent purchases and transaction prices from bounded R3 settlement evidence."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import time
from uuid import uuid4

from market_state import read, write
from r3_policy import selling_terms


def settlement_price(data):
    amount = data.get("amount", data.get("price"))
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
        return None
    if "price" in data and data["price"] != amount:
        return None
    return amount


def price_stats(trades):
    values = [t["amount"] for t in trades]
    return {"count": len(values), "min": min(values), "median": statistics.median(values), "max": max(values)}


def analyze(rows, catalog, inventory, forum, now):
    trades = {}
    for row in rows[-10000:]:
        if not isinstance(row, dict):
            continue
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue
        ts, seq, sku = row.get("ts"), row.get("seq"), data.get("item")
        amount = settlement_price(data)
        if (row.get("round") != 3 or row.get("kind") != "settle" or type(seq) is not int or seq <= 0
                or type(ts) not in (int, float) or not math.isfinite(ts) or not 0 <= now-ts <= 86400
                or not isinstance(sku, str) or not sku or amount is None):
            continue
        product = catalog.get(sku, {})
        warranty = data.get("warranty_hours") or 0
        if type(warranty) not in (int, float) or not math.isfinite(warranty) or warranty < 0:
            warranty = None
        grade = data.get("grade") if data.get("grade") in ("A", "B", "C") else "unknown"
        trades.setdefault(seq, {"ledger": seq, "ts": ts, "buyer": data.get("buyer") if isinstance(data.get("buyer"), str) else None,
                               "shop": data.get("shop") if isinstance(data.get("shop"), str) else None, "sku": sku, "name": product.get("name", sku),
                               "category": product.get("category", "unknown"), "amount": amount,
                               "grade": grade, "warranty_hours": warranty,
                               "unit": data.get("unit"), "expires_at": data.get("expires_at")})
    trades = sorted(trades.values(), key=lambda t: (t["ts"], t["ledger"]))
    by_sku, by_buyer = defaultdict(list), defaultdict(list)
    for trade in trades:
        by_sku[trade["sku"]].append(trade)
        if isinstance(trade["buyer"], str) and trade["buyer"]:
            by_buyer[trade["buyer"]].append(trade)
    requests = {}
    if forum.get("status") == "ok" and 0 <= now-forum.get("observed_at", 0) <= 3600:
        requests = {d["sku"]: d["distinct_requesters"] for d in forum.get("summary", {}).get("demand_skus", [])}
    products = []
    for sku, entries in by_sku.items():
        conditions = defaultdict(list)
        for entry in entries:
            conditions[(entry["grade"], entry["warranty_hours"])].append(entry)
        products.append({"sku": sku, "name": entries[-1]["name"], "category": entries[-1]["category"],
                         "prices": price_stats(entries), "distinct_buyers": len({e["buyer"] for e in entries if e["buyer"]}),
                         "distinct_shops": len({e["shop"] for e in entries if e["shop"]}),
                         "settles_6h": sum(now-e["ts"] <= 21600 for e in entries),
                         "forum_requesters": requests.get(sku, 0),
                         "conditions": [{"grade": grade, "warranty_hours": warranty, "prices": price_stats(group)}
                                        for (grade, warranty), group in conditions.items()],
                         "last_trades": entries[-3:]})
    products.sort(key=lambda p: (-p["prices"]["count"], p["sku"]))
    buyers = []
    for buyer, entries in by_buyer.items():
        sku_groups = defaultdict(list)
        for entry in entries:
            sku_groups[entry["sku"]].append(entry)
        patterns = [{"sku": sku, "name": group[-1]["name"], "prices": price_stats(group),
                     "repeat_observed": len(group) >= 2, "shops": sorted({t["shop"] for t in group if isinstance(t["shop"], str)})}
                    for sku, group in sku_groups.items()]
        patterns.sort(key=lambda p: (-p["prices"]["count"], p["sku"]))
        buyers.append({"buyer": buyer, "settled_count": len(entries), "observed_spend": sum(t["amount"] for t in entries),
                       "categories": dict(Counter(t["category"] for t in entries)), "top_skus": patterns[:10],
                       "last_purchases": entries[-5:]})
    buyers.sort(key=lambda b: (-b["settled_count"], b["buyer"]))
    own = []
    for unit in inventory.get("units", []):
        entries = by_sku.get(unit.get("sku"), [])
        comparable = [e for e in entries if e["grade"] == unit.get("grade") and e["warranty_hours"] == 0]
        try:
            quote = selling_terms(unit, catalog, now=now)["quote"]
        except (KeyError, ValueError, TypeError):
            quote = None
        median = statistics.median(t["amount"] for t in comparable) if comparable else None
        cost = unit.get("cost")
        own.append({"unit": unit.get("unit"), "sku": unit.get("sku"), "name": unit.get("name"), "cost": cost,
                    "grade": unit.get("grade"), "market_prices_all_conditions": price_stats(entries) if entries else None,
                    "same_grade_zero_warranty_prices": price_stats(comparable) if comparable else None,
                    "median_minus_own_cost": median-cost if median is not None and type(cost) in (int, float) else None,
                    "policy_reference_quote": quote,
                    "quote_minus_comparable_median": quote-median if quote is not None and median is not None else None,
                    "thin_sample": len(comparable) < 3, "forum_requesters": requests.get(unit.get("sku"), 0)})
    own_skus = {u.get("sku") for u in inventory.get("units", [])}
    bounded_products = products[:100] + [p for p in products[100:] if p["sku"] in own_skus]
    return {"settled_count": len(trades), "buyers_observed": len(buyers), "skus_observed": len(products),
            "buyers": buyers[:100], "products": bounded_products, "recent_purchases": trades[-20:], "own_stock_comparison": own,
            "note": "Bounded observed R3 purchases, not complete market shares or private buyer policies. Prices are per settled transaction. Grade/warranty groups remain separate; food remaining life and unobserved terms can still differ. Quotes are policy references, not promised offers. Original prices do not include later refunds."}


def observe(md, now=None, force=False, snapshot=None):
    md = Path(md)
    now = time.time() if now is None else now
    try:
        previous = read(md/"purchase-intel.json", {})
    except (OSError, ValueError, TypeError):
        previous = {}
    interval = 1800 if previous.get("status") == "ok" else 60
    if not force and 0 <= now-previous.get("observed_at", 0) < interval:
        return previous
    result = {"observed_at": now, "interval_seconds": 1800, "model_calls": 0, "status": "error"}
    try:
        snapshot = snapshot if snapshot is not None else read(md/"market-intel.json", {})
        result["source_observed_at"] = snapshot.get("observed_at")
        if snapshot.get("round") != 3 or not 0 <= now-snapshot.get("observed_at", 0) <= 3600:
            result["status"] = "stale_or_not_r3"
        else:
            # Confirmed research buys supplement external comparison prices only.
            from buyer_support import research_rows
            local_research = research_rows(md, now)
            combined = {r["seq"]: r for r in local_research}
            combined.update({r["seq"]: r for r in snapshot.get("rows", []) if isinstance(r, dict) and type(r.get("seq")) is int})
            combined_rows = sorted(combined.values(), key=lambda r: (r["ts"] if type(r.get("ts")) in (int, float) and math.isfinite(r["ts"]) else 0, r["seq"]))
            result.update(status="ok", research_price_rows=len(local_research), summary=analyze(combined_rows, read(md/"catalog.json", {}),
                          read(md/"inventory.json", {}), read(md/"forum-intel.json", {}), now),
                          skipped_seq_ranges=snapshot.get("skipped_seq_ranges", []), bounded_sample=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    write(md/"purchase-intel.json", result)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(md.parent/"logs/purchase-monitor"/(stamp+"-"+uuid4().hex[:8]+".json"), result)
    return result


if __name__ == "__main__":
    result = observe(Path(__file__).resolve().parent/".market", force=True)
    summary = result.get("summary", {})
    print(json.dumps({"status": result["status"], "settled_count": summary.get("settled_count"),
                      "buyers": summary.get("buyers_observed"), "skus": summary.get("skus_observed"),
                      "top_products": [{k: p[k] for k in ("sku", "name", "prices", "distinct_buyers")}
                                       for p in summary.get("products", [])[:5]],
                      "own_stock_comparison": summary.get("own_stock_comparison"), "model_calls": 0}, ensure_ascii=False, indent=2))
