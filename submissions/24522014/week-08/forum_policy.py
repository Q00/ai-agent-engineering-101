"""Queue a bounded factual buyer report using the course outbox contract."""

from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import time

from market_state import read, write


def message_key(message):
    return hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def enqueue(md, message, key, kind, metadata=None, now=None):
    """Serialize our writers; official kit sends the existing outbox contract."""
    from strategy_controller import acquire, release
    md = Path(md)
    md.mkdir(parents=True, exist_ok=True)
    handle = acquire(md / "forum-policy.lock")
    if handle is None:
        return "busy"
    try:
        return _enqueue(md, message, key, kind, metadata or {}, time.time() if now is None else now)
    finally:
        release(handle)


def _enqueue(md, message, key, kind, metadata, now):
    state = read(md / "forum-policy.json", {"ledger_refs": [], "last_at": 0, "day": "", "count": 0})
    actions = read(md / "forum-actions.json", {})
    if key in actions or (kind == "buyer_report" and metadata["ledger"] in state["ledger_refs"]):
        return "already_queued"
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    role = message["as"]
    limits = state if role == "buyer" else state.get("shop", {"last_at": 0, "day": "", "count": 0})
    count = limits["count"] if limits["day"] == day else 0
    if count >= 4 or now-limits["last_at"] < 3600:
        return "rate_limited"
    reports_today = sum(a["kind"] == "buyer_report" and
                        datetime.fromtimestamp(a["queued_at"], timezone.utc).date().isoformat() == day
                        for a in actions.values())
    if kind == "buyer_report" and reports_today >= 3:
        return "rate_limited"  # Reserve the fourth buyer slot for a mature follow-up.
    with (md / "outbox.jsonl").open("a", encoding="utf8") as stream:
        stream.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
    limits.update(last_at=now, day=day, count=count+1)
    if role == "shop":
        state["shop"] = limits
    if kind == "buyer_report":
        state["ledger_refs"] = (state["ledger_refs"]+[metadata["ledger"]])[-1000:]
    snapshot = read(md / "market-intel.json", {})
    own = snapshot.get("competition", {}).get("own", {}) if (
        snapshot.get("round") == 3 and 0 <= now-snapshot.get("observed_at", 0) <= 3600) else {}
    board_before = {"at": snapshot.get("observed_at"),
                    "visits": (own.get("shops") or {}).get("visits"),
                    "forum": (own.get("buyers") or {}).get("forum")}
    actions[key] = {"queued_at": now, "kind": kind, "role": role, "message_key": message_key(message),
                    "message": message, "board_before": board_before, **metadata}
    write(md / "forum-policy.json", state)
    write(md / "forum-actions.json", dict(list(actions.items())[-1000:]))
    return "queued"


def comparison_text(md, settled, price, now):
    """Same SKU/grade/warranty goods only; public prices are context, not self evidence."""
    from purchase_monitor import settlement_price
    import statistics
    snapshot = read(md / "market-intel.json", {})
    product = read(md / "catalog.json", {}).get(settled["sku"], {})
    if product.get("kind") != "goods" or snapshot.get("round") != 3 or not 0 <= now-snapshot.get("observed_at", 0) <= 3600:
        return " Comparable price sample unavailable; no cheapest-price claim."
    rows = {}
    for row in snapshot.get("rows", [])[-10000:]:
        data = row.get("data") or {}
        amount = settlement_price(data)
        if (row.get("round") == 3 and row.get("kind") == "settle"
                and type(row.get("seq")) is int and row["seq"] != settled["ledger"]
                and type(row.get("ts")) in (int, float) and 0 <= now-row["ts"] <= 86400
                and data.get("item") == settled["sku"] and data.get("grade") == settled.get("grade")
                and (data.get("warranty_hours") or 0) == (settled.get("warranty_hours") or 0) and amount is not None):
            rows[row["seq"]] = amount
    if len(rows) < 3:
        return " Comparable price sample below 3; no cheapest-price claim."
    median = statistics.median(rows.values())
    delta = (price / median - 1) * 100
    return (f" Observed same-SKU/grade/warranty goods sample n={len(rows)}, median={median:g}; "
            f"paid {abs(delta):.1f}% {'below' if delta < 0 else 'above'} sample median. "
            "Sample is incomplete; later refunds excluded.")


def queue_report(md, deal, result, now=None):
    now = time.time() if now is None else now
    settled, receipt = (deal.get(k) or {} for k in ("settled", "receipt"))
    ledger = settled.get("ledger")
    if result.get("stars") != 5 or type(ledger) is not int or ledger <= 0:
        return "insufficient_evidence"
    price, value = receipt.get("price"), settled.get("value")
    if (type(price) not in (int, float) or type(value) not in (int, float)
            or not math.isfinite(price) or not math.isfinite(value) or price <= 0 or value <= 0 or not settled.get("sku")):
        return "insufficient_evidence"
    md = Path(md)
    md.mkdir(parents=True, exist_ok=True)
    text = (f"Buyer settlement report #{ledger}: {str(settled['sku'])[:80]}, paid {price} credits, "
            f"market value at settlement {value}, initial surplus {value-price}. "
            f"Recorded grade {settled.get('grade')}; warranty {settled.get('warranty_hours') or 0} h. "
            "Receipt and order matched. Later breakage/refunds are not assessed in this report.")
    text += comparison_text(md, settled, price, now)
    message = {"post": text, "refs": [ledger], "as": "buyer"}
    return enqueue(md, message, f"buyer-report:{ledger}", "buyer_report",
                   {"ledger": ledger, "sku": settled["sku"], "unit": settled.get("unit")}, now)
