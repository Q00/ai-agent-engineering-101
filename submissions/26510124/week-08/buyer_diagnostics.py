"""Read-only local buyer diagnostics; no network, models, or state writes.

    python buyer_diagnostics.py --hours 24 --round 3
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from payment_memory import record_time

REASONS = frozenset({"model_walk_away", "seller_rejected", "seller_cancelled",
    "decision_limit", "price_limit", "concession_limit", "receipt_mismatch",
    "model_timeout", "model_error", "protocol_or_network_error", "receipt_issued", "unknown"})
STAGES = frozenset({"discovery", "opening_request", "receipt_validation", "seller_terminal",
    "seller_poll", "decision_limit", "model_decision", "decision_validation", "buyer_cancel", "offer_request"})
ROOT = Path(__file__).resolve().parent


def read_tail(path, *, byte_limit=4_000_000, row_limit=5000):
    """Skip partial/malformed lines but expose coverage rather than hiding it."""
    status = {"status": "missing", "rows": 0, "malformed": 0, "partial_tail": False,
              "byte_truncated": False, "row_truncated": False}
    try:
        with Path(path).open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - byte_limit)
            stream.seek(start)
            raw = stream.read(byte_limit)
    except FileNotFoundError:
        return [], status
    except OSError as exc:
        status.update(status="unreadable", error_class=type(exc).__name__)
        return [], status
    status.update(status="read", byte_truncated=start > 0)
    lines = raw.splitlines()
    if start:
        lines = lines[1:]
    if raw and not raw.endswith(b"\n"):
        status["partial_tail"] = True
        lines = lines[:-1]
    status["row_truncated"] = len(lines) > row_limit
    result = []
    for line in lines[-row_limit:]:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not object")
        except (ValueError, UnicodeError):
            status["malformed"] += 1
            continue
        result.append(row)
    status["rows"] = len(result)
    return result, status


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _ledger(value):
    return type(value) is int and value > 0


def _task(row):
    receipt = row.get("observed_receipt")
    diag = row.get("negotiation_diagnostics")
    for value in ((receipt or {}).get("task") if isinstance(receipt, dict) else None,
                  (diag or {}).get("task_id") if isinstance(diag, dict) else None):
        if isinstance(value, str) and value:
            return value
    return None


def _payment_key(row):
    key = row.get("order"), row.get("url"), row.get("item"), row.get("price")
    return key if all(isinstance(v, str) and v for v in key[:3]) and _number(key[3]) and key[3] > 0 else None


def _review_matches(record, review):
    receipt, settled = review.get("receipt"), review.get("settled")
    if not isinstance(receipt, dict) or not isinstance(settled, dict):
        return False
    task = _task(record)
    if not (task and review.get("order") == record.get("order")
            and receipt.get("task") == task and receipt.get("item") == record.get("item")
            and _number(receipt.get("price")) and receipt["price"] == record.get("price")
            and _ledger(settled.get("ledger")) and settled.get("shop") == record.get("url")
            and settled.get("sku") == record.get("item")):
        return False
    for key, expected in (("round", record["round"]), ("order", record["order"]), ("ok", True)):
        if key in settled and (settled[key] != expected or type(settled[key]) is not type(expected)):
            return False
    for key in ("price", "amount"):
        if key in settled and (not _number(settled[key]) or settled[key] != record["price"]):
            return False
    observed = record.get("observed_receipt")
    observed = observed if isinstance(observed, dict) else {}
    for key in ("unit", "shop"):
        if key in observed and key in receipt and receipt[key] != observed[key]:
            return False
    if "unit" in observed and "unit" in settled and observed["unit"] != settled["unit"]:
        return False
    return True


def summarize(root=ROOT, *, hours=24, round_number=3, now=None, byte_limit=4_000_000, row_limit=5000):
    if not _number(hours) or not 0 < hours <= 24 * 31:
        raise ValueError("hours must be between zero and 744")
    now = time.time() if now is None else now
    if not _number(now) or type(round_number) is not int or round_number < 1:
        raise ValueError("invalid diagnostic window")
    root = Path(root)
    paths = {"memory": root / "data" / "market-memory.jsonl",
             "payments": root / ".market" / "payments.jsonl",
             "settled_deals": root / "logs" / "settled-deals.jsonl",
             "selection_events": root / "data" / "buyer-events.jsonl"}
    rows, sources = {}, {}
    for name, path in paths.items():
        rows[name], sources[name] = read_tail(path, byte_limit=byte_limit, row_limit=row_limit)
    excluded = Counter()
    records, fingerprints, conflicting = {}, defaultdict(set), set()
    for row in rows["memory"]:
        if type(row.get("round")) is not int or row["round"] != round_number:
            excluded["other_or_missing_round"] += 1
            continue
        stamp = record_time(row.get("time"))
        if stamp is None:
            excluded["missing_or_invalid_time"] += 1
            continue
        if not now - hours * 3600 <= stamp <= now:
            excluded["future_time" if stamp > now else "outside_window"] += 1
            continue
        if not isinstance(row.get("order"), str) or not row["order"]:
            excluded["missing_order"] += 1
            continue
        key = (row["order"], _task(row) or row.get("url"))
        if not isinstance(key[1], str):
            excluded["missing_task_and_shop"] += 1
            continue
        if key in records:
            excluded["duplicate_attempt_rows"] += 1
        records[key] = row
        fingerprints[key].add(json.dumps([row.get(k) for k in ("order", "round", "url", "item", "price")], sort_keys=True))
        if len(fingerprints[key]) > 1:
            conflicting.add(key)
    attempts_per_payment = defaultdict(set)
    for key, row in records.items():
        if row.get("result") == "receipt_issued" and _payment_key(row) is not None:
            attempts_per_payment[_payment_key(row)].add(key)
    payment_index, review_index = defaultdict(list), defaultdict(list)
    for row in rows["payments"]:
        if _number(row.get("price")) and row["price"] > 0:
            key = (row.get("order"), row.get("shop"), row.get("item"), row["price"])
            if all(isinstance(v, str) and v for v in key[:3]):
                payment_index[key].append(row)
    for row in rows["settled_deals"]:
        if isinstance(row.get("order"), str):
            review_index[row["order"]].append(row)
    reasons, stages, seller_reports = Counter(), Counter(), Counter()
    statuses, proofs, durations, decisions = {}, {}, [], []
    decision_sources = {"fast_accepts": [], "model_decisions": []}
    fast_skips, fast_skip_attempts = Counter(), 0
    skip_codes = {"not_applicable", "invalid_quote_parts", "structured_quote_missing_or_ambiguous",
                  "invalid_quote_identity_or_price", "price_above_limit", "price_above_target",
                  "invalid_quote_metadata", "quote_metadata_conflict", "text_price_missing_or_conflicting",
                  "certificate_missing_or_invalid", "grade_not_accepted_or_conflicting",
                  "warranty_missing_or_insufficient", "food_policy_not_supported",
                  "food_clock_or_expiry_unavailable", "food_lifetime_or_checkout_headroom_insufficient",
                  "food_expiry_conflict", "unsupported_kind"}
    for key, row in records.items():
        diag = row.get("negotiation_diagnostics")
        valid = isinstance(diag, dict) and type(diag.get("version")) is int and diag["version"] == 1
        reason = diag.get("reason") if valid else None
        reasons[reason if isinstance(reason, str) and reason in REASONS else "unknown"] += 1
        stage = diag.get("stage") if valid else None
        stages[stage if isinstance(stage, str) and stage in STAGES else "unknown"] += 1
        if valid:
            skips = diag.get("fast_accept_skip_reasons")
            if isinstance(skips, dict):
                fast_skip_attempts += 1
                for code, count in skips.items():
                    if type(count) is int and count >= 0:
                        fast_skips[code if code in skip_codes else "unknown"] += count
            for field, counts in decision_sources.items():
                if type(diag.get(field)) is int and diag[field] >= 0:
                    counts.append(diag[field])
            elapsed = diag.get("elapsed_seconds")
            if _number(elapsed) and elapsed >= 0:
                durations.append(elapsed)
            if type(diag.get("decisions")) is int and diag["decisions"] >= 0:
                decisions.append(diag["decisions"])
            seller = diag.get("seller_terminal")
            if isinstance(seller, dict) and seller.get("source") == "untrusted_seller":
                tags = seller.get("reported_tags")
                tags = tags if isinstance(tags, list) else []
                for tag in {tag for tag in tags if isinstance(tag, str)} & {"stock", "price"}:
                    seller_reports[tag] += 1
        if row.get("result") != "receipt_issued":
            statuses[key] = "no_receipt_observed"
            continue
        if key in conflicting or _payment_key(row) is None:
            statuses[key] = "conflicting_or_invalid_evidence"
            continue
        ledgers, refusal = set(), False
        pkey = _payment_key(row)
        if len(attempts_per_payment[pkey]) == 1:
            for payment in payment_index[pkey]:
                if "round" in payment and (type(payment["round"]) is not int or payment["round"] != round_number):
                    continue
                if "task" in payment and payment["task"] != _task(row):
                    continue
                if payment.get("paid") is True and _ledger(payment.get("ledger")):
                    ledgers.add(payment["ledger"])
                elif payment.get("paid") is False:
                    refusal = True
        for review in review_index[row["order"]]:
            if _review_matches(row, review):
                ledgers.add(review["settled"]["ledger"])
        if len(ledgers) > 1:
            statuses[key] = "conflicting_or_invalid_evidence"
        elif ledgers:
            proofs[key] = next(iter(ledgers))
            statuses[key] = "confirmed_settlement"
        else:
            statuses[key] = "payment_refusal_observed" if refusal else "receipt_unconfirmed"
    # One ledger event cannot prove two different orders/tasks, and an order
    # settles only once. Conflicting evidence never increases the sales count.
    by_ledger, by_order = defaultdict(list), defaultdict(list)
    for key, ledger in proofs.items():
        by_ledger[ledger].append(key)
        by_order[key[0]].append(key)
    for keys in list(by_ledger.values()) + list(by_order.values()):
        if len(keys) > 1:
            for key in keys:
                statuses[key] = "conflicting_or_invalid_evidence"
    selections = {}
    for row in rows["selection_events"]:
        stamp = record_time(row.get("time"))
        if (row.get("event") == "selection_failure" and type(row.get("round")) is int
                and row["round"] == round_number and stamp is not None
                and now - hours * 3600 <= stamp <= now and isinstance(row.get("order"), str)):
            selections[row["order"]] = row
    status_counts = Counter(statuses.values())
    return {"version": 1, "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "round": round_number, "hours": hours, "attempts": len(records),
            "distinct_orders": len({key[0] for key in records}),
            "terminal_reasons": dict(reasons), "stages": dict(stages),
            "seller_reported_unconfirmed": dict(seller_reports),
            "settlement_status": dict(status_counts),
            "confirmed_settlements": status_counts["confirmed_settlement"],
            "receipts_observed": sum(row.get("result") == "receipt_issued" for row in records.values()),
            "selection_failures": len(selections),
            "selection_failure_reasons": dict(Counter(row.get("reason") if isinstance(row.get("reason"), str) and row["reason"] in REASONS else "unknown" for row in selections.values())),
            "decisions_total": sum(decisions), "attempts_with_decision_count": len(decisions),
            "decision_sources": {field: {"total": sum(counts), "attempts_observed": len(counts)}
                                 for field, counts in decision_sources.items()},
            "fast_accept_skips": {"reasons": dict(fast_skips), "attempts_observed": fast_skip_attempts,
                                  "basis": "First blocking check per decision; not unique failed orders. Older rows may lack telemetry."},
            "elapsed_seconds_total": round(sum(durations), 3), "attempts_with_elapsed": len(durations),
            "excluded_rows": dict(excluded), "sources": sources,
            "limits": ["Bounded local tails, not all market orders or lifetime history.",
                "Window uses local negotiation record time; undated payment/review rows count only when correlated to current-round attempts.",
                "Receipt issued or missing payment evidence is unconfirmed, not a failed payment.",
                "Observed payment refusal may have another later outcome outside the bounded evidence.",
                "Old records without structured diagnostics have unknown reasons; no text-based cause inference.",
                "Seller stock/price reports are untrusted claims, separate from local terminal reason.",
                "No network requests, model calls, raw budgets/signatures, or state writes."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--round", type=int, default=3, dest="round_number")
    parser.add_argument("--round3", action="store_const", const=3, dest="round_number")
    args = parser.parse_args(argv)
    try:
        report = summarize(args.root, hours=args.hours, round_number=args.round_number)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
