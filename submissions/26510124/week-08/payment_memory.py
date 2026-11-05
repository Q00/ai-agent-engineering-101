"""Bounded views of append-only evidence; never rewrite the original logs."""
import json
import math
import re
from datetime import datetime
from pathlib import Path


def recent_jsonl(path, *, limit=2_000_000, rows=2000):
    try:
        with Path(path).open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - limit)
            stream.seek(start)
            raw = stream.read(limit)
    except FileNotFoundError:
        return []
    lines = raw.splitlines()
    if start:
        lines = lines[1:]
    if raw and not raw.endswith(b"\n"):
        lines = lines[:-1]  # concurrent append: only complete records
    out = []
    for line in lines[-rows:]:
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON objects in memory")
        out.append(value)
    return out


def record_time(value):
    """Only an explicit timezone makes a local receipt time usable for expiry."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        stamp = parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return stamp if stamp is not None and math.isfinite(stamp) else None


def acceptance_refusal(reason, policy):
    """Recognize only the pinned kit's local, pre-payment grade/warranty check.

    Seller messages, network failures and generic checkout refusals never count
    as evidence of incompatible purchase conditions. No arbitrary text parser
    or model inference is used here.
    """
    if not isinstance(reason, str) or len(reason) > 240 or not isinstance(policy, dict):
        return None
    accept = policy.get("accept")
    if not isinstance(accept, dict):
        return None
    grades = accept.get("grades")
    if (isinstance(grades, list) and 1 <= len(grades) <= 3
            and all(isinstance(grade, str) and grade in ("A", "B", "C") for grade in grades)
            and len(set(grades)) == len(grades)):
        for grade in ("A", "B", "C"):
            if grade not in grades and reason == f"not paying: grade {grade}, the policy accepts {grades!r}":
                return {"source": "kit_acceptance", "kind": "grade", "observed": grade, "required": list(grades)}
    required = accept.get("warranty_hours")
    if (policy.get("kind") != "goods" or type(required) not in (int, float)
            or not math.isfinite(required) or required <= 0):
        return None
    match = re.fullmatch(r"not paying: warranty ([0-9]+(?:\.[0-9]+)?) h, the policy asks for ([0-9]+(?:\.[0-9]+)?)", reason)
    if match is not None:
        observed, quoted_required = map(float, match.groups())
        if math.isfinite(observed) and quoted_required == required and 0 <= observed < required:
            return {"source": "kit_acceptance", "kind": "warranty", "observed": observed, "required": required}
    return None


def reconcile(records, payments):
    paid = {(p.get("order"), p.get("shop")): p for p in payments
            if isinstance(p, dict) and isinstance(p.get("order"), str) and p["order"]
            and isinstance(p.get("shop"), str) and p["shop"]}
    result = []
    for raw in records:
        record = dict(raw)
        if (type(record.get("round")) is int and record["round"] >= 3
                and record.get("result") == "receipt_issued"
                and isinstance(record.get("order"), str) and isinstance(record.get("url"), str)):
            p = paid.get((record["order"], record["url"]))
            if (p and p.get("item") == record.get("item") and p.get("price") == record.get("price")
                    and type(p.get("price")) in (int, float) and math.isfinite(p["price"]) and p["price"] > 0
                    and type(record.get("price")) in (int, float)
                    and ("round" not in p or type(p["round"]) is int and p["round"] == record["round"])):
                record.pop("policy_refusal", None)
                if p.get("paid") is True:
                    record.update(result="settled", ledger=p.get("ledger"), detail="Kit confirmed payment")
                elif p.get("paid") is False:
                    record.update(result="payment_refused", detail=str(p.get("reason", "unknown"))[:400])
                    refusal = acceptance_refusal(p.get("reason"), record.get("purchase_policy"))
                    stamp = record_time(record.get("time"))
                    if (refusal is not None and stamp is not None
                            and isinstance(record.get("name"), str) and record["name"]
                            and isinstance(record.get("item"), str) and record["item"]
                            and ("time" not in p or record_time(p["time"]) is not None and record_time(p["time"]) >= stamp)):
                        record["policy_refusal"] = refusal
        result.append(record)
    return result


def attach_reviews(records, reviews):
    indexed = {r.get("order"): r for r in reviews if isinstance(r.get("order"), str)}
    result = []
    for raw in records:
        record = dict(raw)
        record["verified_review"] = False
        row = indexed.get(record.get("order"), {})
        receipt, assessment = row.get("receipt", {}), row.get("assessment", {})
        observed = record.get("observed_receipt") or {}
        if (record.get("result") == "settled" and observed.get("task")
                and receipt.get("task") == observed["task"] and receipt.get("price") == record.get("price")
                and receipt.get("item") == record.get("item")
                and type(assessment.get("stars")) is int and 1 <= assessment["stars"] <= 5):
            record.update(stars=assessment["stars"], review=assessment.get("review", ""),
                          review_reasons=assessment.get("reasons", []))
            settled = row.get("settled")
            record["verified_review"] = (
                isinstance(settled, dict) and type(record.get("ledger")) is int and record["ledger"] > 0
                and type(settled.get("ledger")) is int and settled["ledger"] == record["ledger"]
                and isinstance(record.get("url"), str) and settled.get("shop") == record["url"])
            for source, target in (("issues", "review_issues"),
                                   ("preference_shortfalls", "review_preference_shortfalls")):
                codes = assessment.get(source)
                if isinstance(codes, list) and all(isinstance(code, str) for code in codes):
                    record[target] = list(codes)
        result.append(record)
    return result
