"""Bounded verified same-SKU prices, grouped by actual delivered quality.

Historical prices are selection/negotiation references, never current quotes
or purchase authorization. There are no model, network or disk operations.
"""
import json
import math
from statistics import median
import time

from buyer_policy import buying_terms
from payment_memory import record_time

MAX_ROWS = 2000
MAX_AGE_SECONDS = 6 * 3600
MIN_SAMPLES = 2
MAX_GROUPS = 2


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _identity(row):
    """Only price-proof identity matters; review prose is not evidence."""
    fields = ("round", "name", "url", "item", "order", "ledger", "price", "time", "result",
              "verified_review", "review_issues", "observed_receipt", "settled_value_evidence")
    try:
        return json.dumps({key: row.get(key) for key in fields}, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None


def _actual(row):
    evidence, receipt = row.get("settled_value_evidence"), row.get("observed_receipt")
    if not (row.get("result") == "settled" and row.get("verified_review") is True
            and row.get("review_issues") == []
            and type(row.get("ledger")) is int and row["ledger"] > 0
            and isinstance(row.get("order"), str) and row["order"]
            and isinstance(row.get("url"), str) and row["url"]
            and isinstance(evidence, dict) and isinstance(receipt, dict)):
        return None
    price = row.get("price")
    if (not _number(price) or price <= 0 or price != int(price)
            or not isinstance(receipt.get("task"), str) or not receipt["task"]
            or receipt.get("item") != row["item"]
            or not _number(receipt.get("price")) or receipt["price"] != price
            or evidence.get("ledger") != row["ledger"] or type(evidence.get("ledger")) is not int
            or any(evidence.get(field) != row[source] for field, source in (
                ("order", "order"), ("shop", "url"), ("sku", "item")))
            or evidence.get("task") != receipt["task"]
            or not _number(evidence.get("price")) or evidence["price"] != price
            or evidence.get("grade") not in ("A", "B", "C")
            or evidence.get("kind") not in (None, "goods")
            or evidence.get("claim_honest") is not True
            or not _number(evidence.get("value")) or evidence["value"] <= 0
            or not _number(evidence.get("warranty_hours")) or not 0 <= evidence["warranty_hours"] <= 8760):
        return None
    for source in (row, evidence):
        if any(key in source and (not _number(source[key]) or source[key] != 0)
               for key in ("refund", "refunded", "refund_amount")):
            return None
        if "refunds" in source and source["refunds"] != []:
            return None
    return evidence


def build_value_evidence(memory, *, catalog, order, budget, config, now=None):
    """Map (shop name, SKU) to at most two actual grade/warranty groups.

    Current-round, six-hour, confirmed and fault-free goods settlements only.
    Catalog kind supplies the product type absent from the pinned settlement
    receipt; food freshness and delayed breakage are not inferred here.
    """
    now = time.time() if now is None else now
    if (not _number(now) or not isinstance(memory, list) or not isinstance(catalog, dict)
            or not isinstance(config, dict) or not config or type(budget) is not int or budget <= 0
            or not isinstance(order, dict) or type(order.get("round")) is not int
            or order["round"] < 3 or order.get("kind") != "goods"):
        return {}
    rows = [row for row in memory[-MAX_ROWS:] if isinstance(row, dict)
            and type(row.get("round")) is int and row["round"] == order["round"]
            and isinstance(row.get("name"), str) and row["name"]
            and isinstance(row.get("item"), str) and row["item"]]
    ledger_ids, order_ids, blocked_after, uncertain = {}, {}, {}, set()
    for row in rows:
        key, stamp = (row["name"], row["item"]), record_time(row.get("time"))
        fault = (row.get("result") in ("payment_refused", "receipt_mismatch")
                 or row.get("verified_review") is True and isinstance(row.get("review_issues"), list)
                 and bool(row["review_issues"]))
        if fault:
            if stamp is None or stamp > now:
                uncertain.add(key)
            elif now - MAX_AGE_SECONDS <= stamp:
                blocked_after[key] = max(stamp, blocked_after.get(key, -math.inf))
        # Include malformed/conflicting duplicates in the identity maps too;
        # keeping only valid rows could hide a contradictory newer record.
        signature = _identity(row)
        if type(row.get("ledger")) is int and row["ledger"] > 0:
            ledger_ids.setdefault(row["ledger"], set()).add(signature)
        if isinstance(row.get("order"), str) and row["order"]:
            order_ids.setdefault(row["order"], set()).add(signature)
    groups, seen = {}, set()
    acceptable = order.get("acceptable")
    for row in rows:
        key, stamp = (row["name"], row["item"]), record_time(row.get("time"))
        if (key in uncertain or stamp is None or not 0 <= now - stamp <= MAX_AGE_SECONDS
                or stamp <= blocked_after.get(key, -math.inf)
                or isinstance(acceptable, list) and row["item"] not in acceptable):
            continue
        evidence = _actual(row)
        if evidence is None:
            continue
        ledger, order_id = row["ledger"], row["order"]
        if (ledger in seen or len(ledger_ids[ledger]) != 1 or None in ledger_ids[ledger]
                or len(order_ids[order_id]) != 1 or None in order_ids[order_id]):
            continue
        product = catalog.get(row["item"])
        if not isinstance(product, dict) or product.get("kind") != "goods":
            continue
        try:
            terms = buying_terms(budget, product=product, order=order, config=config)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        accept = terms["accept"]
        if (not accept.get("cert") or evidence["grade"] not in accept.get("grades", [])
                or evidence["warranty_hours"] < accept.get("warranty_hours", 0)
                or evidence["value"] != terms["list_price"] or row["price"] > terms["price_limit"]):
            continue
        seen.add(ledger)
        group = groups.setdefault(key, {}).setdefault((evidence["grade"], evidence["warranty_hours"]), [])
        group.append((int(row["price"]), stamp, terms["list_price"]))
    result = {}
    for key, quality_groups in groups.items():
        summaries = []
        for (grade, warranty), samples in quality_groups.items():
            if len(samples) < MIN_SAMPLES:
                continue
            price = math.ceil(median(sample[0] for sample in samples))
            summaries.append({"kind": "goods", "grade": grade, "warranty_hours": warranty,
                "sample_count": len(samples), "median_price": price,
                "price_to_list": round(price / samples[0][2], 6),
                "age_seconds": math.ceil(now - max(sample[1] for sample in samples))})
        summaries.sort(key=lambda summary: (summary["price_to_list"], -summary["sample_count"],
                                             summary["age_seconds"], summary["grade"], -summary["warranty_hours"]))
        if summaries:
            result[key] = {"groups": summaries[:MAX_GROUPS], "best_price_to_list": summaries[0]["price_to_list"]}
    return result
