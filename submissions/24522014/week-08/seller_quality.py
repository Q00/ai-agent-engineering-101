"""Attribute public reviews only to our confirmed local ledger receipts; no LLM."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import ssl
import statistics
import time
from uuid import uuid4

import httpx
from market_state import read, write


def observe(md, now=None, reviews=None):
    md = Path(md)
    now = time.time() if now is None else now
    previous = read(md / "seller-quality.json", {})
    if 0 <= now-previous.get("observed_at", -86400) < 1800:
        return previous
    try:
        if reviews is None:
            with httpx.Client(verify=ssl.create_default_context(), timeout=8) as client:
                response = client.get("https://market.ourolabs.ai/api/shops")
                response.raise_for_status()
                registry = response.json()
            shops = registry.get("shops", []) if isinstance(registry, dict) else registry
            mine = [s for s in shops if str(s.get("name", "")).casefold() == "kcwarni"]
            if len(mine) != 1:
                raise ValueError("Own shop identity is ambiguous")
            reviews = mine[0].get("reviews", [])
        local = read(md / "local-settlements.json", {})
        if (local.get("active_round") != 3 or not local.get("r3_anchor_found") or local.get("conflicting_ledgers")
                or not 0 <= now-local.get("collected_at", 0) <= 3600):
            raise ValueError("Fresh local R3 receipt index required")
        records = local.get("records", {})
        units = defaultdict(set)
        for path in (md / "signed-offers").glob("*.json"):
            offer = read(path)
            if offer.get("unit") and offer.get("sku"):
                units[offer["unit"]].add(offer["sku"])
        history = {str(r["ledger"]): r for r in previous.get("reviews", []) if 0 <= now-r["sale_at"] <= 86400}
        unmatched = 0
        for review in reviews:
            if review.get("round") != 3 or type(review.get("stars")) not in (int, float) or not 1 <= review["stars"] <= 5:
                continue
            text = str(review.get("review", ""))[:2000]
            refs = set(re.findall(r"\b(?:ledger|settlement)\s*#?\s*(\d+)", text, re.I))
            if len(refs) != 1:
                unmatched += 1
                continue
            ledger = refs.pop()
            sale = records.get(ledger, {})
            skus = units.get(sale.get("unit"), set())
            if sale.get("source") != "local_acp_settled" or sale.get("round") != 3 or len(skus) != 1:
                unmatched += 1
                continue
            ratio = re.search(r"paid\s+(\d+(?:\.\d+)?)%\s+of\s+list", text, re.I)
            price_hint = review["stars"] <= 3 and (bool(ratio and float(ratio[1]) >= 85)
                            or bool(re.search(r"too expensive|overpriced|비싸|비싼", text, re.I)))
            history[ledger] = {"ledger": int(ledger), "sku": next(iter(skus)), "sale_at": sale["ts"],
                               "stars": review["stars"], "price_hint": price_hint, "excerpt": text[:180]}
        history = dict(sorted(history.items(), key=lambda p: p[1]["sale_at"])[-500:])
        grouped = defaultdict(list)
        for review in history.values():
            if 0 <= now-review["sale_at"] <= 21600:
                grouped[review["sku"]].append(review)
        skus = {}
        for sku, group in grouped.items():
            mean = statistics.mean(r["stars"] for r in group)
            skus[sku] = {"count": len(group), "mean_stars": mean, "low_reviews": sum(r["stars"] <= 3 for r in group),
                         "price_hints": sum(r["price_hint"] for r in group),
                         "weak": len(group) >= 3 and mean < 4.5,
                         "strong": len(group) >= 3 and mean >= 4.7 and not any(r["stars"] <= 3 for r in group)}
        result = {"status": "ok", "observed_at": now, "skus": skus, "reviews": list(history.values()),
                  "unmatched_current_reviews": unmatched, "model_calls": 0,
                  "note": "Bounded public review sample; price hints are hypotheses, not proven causes."}
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError) as error:
        result = {**previous, "status": "unavailable", "observed_at": now,
                  "error": f"{type(error).__name__}: {error}", "model_calls": 0}
    write(md / "seller-quality.json", result)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(md.parent / "logs/seller-quality" / (stamp+"-"+uuid4().hex[:8]+".json"), result)
    return result


def fresh(md, now=None):
    now = time.time() if now is None else now
    data = read(Path(md) / "seller-quality.json", {})
    return data.get("skus", {}) if data.get("status") == "ok" and 0 <= now-data.get("observed_at", 0) <= 3600 else {}
