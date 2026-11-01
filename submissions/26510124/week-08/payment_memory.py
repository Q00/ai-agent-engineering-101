"""Bounded views of append-only evidence; never rewrite the original logs."""
import json
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


def reconcile(records, payments):
    paid = {(p.get("order"), p.get("shop")): p for p in payments if isinstance(p.get("order"), str)}
    result = []
    for raw in records:
        record = dict(raw)
        if record.get("round", 0) >= 3 and record.get("result") == "receipt_issued":
            p = paid.get((record.get("order"), record.get("url")))
            if p and p.get("item") == record.get("item") and p.get("price") == record.get("price"):
                if p.get("paid") is True:
                    record.update(result="settled", ledger=p.get("ledger"), detail="Kit confirmed payment")
                elif p.get("paid") is False:
                    record.update(result="payment_refused", detail=str(p.get("reason", "unknown"))[:400])
        result.append(record)
    return result


def attach_reviews(records, reviews):
    indexed = {r.get("order"): r for r in reviews if isinstance(r.get("order"), str)}
    result = []
    for raw in records:
        record = dict(raw)
        row = indexed.get(record.get("order"), {})
        receipt, assessment = row.get("receipt", {}), row.get("assessment", {})
        observed = record.get("observed_receipt") or {}
        if (record.get("result") == "settled" and observed.get("task")
                and receipt.get("task") == observed["task"] and receipt.get("price") == record.get("price")
                and receipt.get("item") == record.get("item")
                and type(assessment.get("stars")) is int and 1 <= assessment["stars"] <= 5):
            record.update(stars=assessment["stars"], review=assessment.get("review", ""),
                          review_reasons=assessment.get("reasons", []))
        result.append(record)
    return result
