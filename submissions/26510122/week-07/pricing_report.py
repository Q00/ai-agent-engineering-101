"""Report the observational seller cohort without dropping unknown outcomes."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from collections import OrderedDict

from pricing import PricingRecorder


def mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 4) if values else None


def summarize(rows: list[dict]) -> dict:
    attempts = len(rows)
    exposed = [row for row in rows if row["opening_ask_actual"] is not None]
    counted = [row for row in rows if row["ledger_status"] == "COUNTED"]
    margin = sum(row["final_price"] - row["reserve"] for row in counted)
    return {
        "recorded_attempts": attempts,
        "exposed_attempts": len(exposed),
        "receipt_issued": sum(bool(row["receipt_issued"]) for row in rows),
        "market_counted": len(counted),
        "negotiation_completed": sum(row["negotiation_status"] == "COMPLETED" for row in rows),
        "explicit_rejected": sum(row["negotiation_status"] == "REJECTED" for row in rows),
        "timed_out": sum(row["negotiation_status"] == "TIMED_OUT" for row in rows),
        "infra_error": sum(row["negotiation_status"] == "INFRA_ERROR" for row in rows),
        "cancelled": sum(row["negotiation_status"] == "CANCELLED" for row in rows),
        "negotiation_unresolved": sum(row["negotiation_status"] in {"OPEN", "RECEIPT_ISSUED"} for row in rows),
        "ledger_pending": sum(row["ledger_status"] == "PENDING" for row in rows),
        "ledger_not_counted": sum(row["ledger_status"] == "NOT_COUNTED" for row in rows),
        "ledger_none": sum(row["ledger_status"] == "NONE" for row in rows),
        "average_opening_ask_over_reserve": mean(
            [row["opening_ask_actual"] / row["reserve"] for row in exposed]),
        "average_counted_final_price": mean([row["final_price"] for row in counted]),
        "total_counted_margin": margin,
        "counted_margin_per_recorded_attempt": round(margin / attempts, 4) if attempts else None,
        "counted_margin_per_exposed_attempt": round(margin / len(exposed), 4) if exposed else None,
        "counted_rate_per_recorded_attempt": round(len(counted) / attempts, 4) if attempts else None,
        "counted_rate_per_exposed_attempt": round(len(counted) / len(exposed), 4) if exposed else None,
        "model_calls_total": sum(row["model_calls"] for row in rows),
        "model_calls_per_recorded_attempt": mean([row["model_calls"] for row in rows]),
    }


def ledger_lag(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT o.task_id, o.ledger_ts, o.observed_at, e.receipt_at
            FROM ledger_observations AS o
            JOIN (SELECT task_id, MIN(at) AS receipt_at FROM events
                  WHERE kind='receipt_issued' GROUP BY task_id) AS e ON e.task_id=o.task_id
            JOIN trades AS t ON t.task_id=o.task_id
              AND t.ledger_evidence='ledger seq ' || o.seq
            WHERE t.ledger_status IN ('COUNTED','NOT_COUNTED')
        """).fetchall()
    market_delays = []
    polling_delays = []
    for row in rows:
        receipt_at = datetime.fromisoformat(row["receipt_at"]).timestamp()
        market_delays.append(float(row["ledger_ts"]) - receipt_at)
        polling_delays.append(datetime.fromisoformat(row["observed_at"]).timestamp() - float(row["ledger_ts"]))
    return {
        "locally_issued_receipts_with_ledger_timing": len(rows),
        "median_receipt_to_ledger_seconds": (
            round(statistics.median(market_delays), 2) if market_delays else None),
        "median_ledger_to_local_reconciliation_seconds": (
            round(statistics.median(polling_delays), 2) if polling_delays else None),
        "min_receipt_to_ledger_seconds": round(min(market_delays), 2) if market_delays else None,
        "max_receipt_to_ledger_seconds": round(max(market_delays), 2) if market_delays else None,
    }


def legacy_log_summary(path: Path, first_attempts: int = 600) -> dict:
    """Read only explicit seller events; absence of an outcome stays UNKNOWN."""
    trades: OrderedDict[str, dict] = OrderedDict()
    with path.open(encoding="utf-8", errors="replace") as source:
        lines = source.readlines()
    for line in lines:
        if "[seller] {" not in line:
            continue
        try:
            event = json.loads(line.split("[seller] ", 1)[1])
        except (ValueError, IndexError):
            continue
        task_id = event.get("task_id")
        kind = event.get("event")
        if kind == "seller_open" and len(trades) < first_attempts:
            trades.setdefault(task_id, {"status": "UNKNOWN", "counters": 0})
        elif task_id in trades and kind == "seller_counter":
            trades[task_id]["counters"] += 1
        elif task_id in trades and kind == "seller_deal":
            trades[task_id]["status"] = "RECEIPT_ISSUED"
        elif task_id in trades and kind == "seller_reject":
            trades[task_id]["status"] = "REJECTED"
    return {
        "source": str(path), "first_attempts_requested": first_attempts,
        "attempts_observed": len(trades),
        "receipt_issued": sum(row["status"] == "RECEIPT_ISSUED" for row in trades.values()),
        "explicit_rejected": sum(row["status"] == "REJECTED" for row in trades.values()),
        "unknown": sum(row["status"] == "UNKNOWN" for row in trades.values()),
        "unknown_after_counter": sum(row["status"] == "UNKNOWN" and row["counters"] > 0
                                     for row in trades.values()),
        "unknown_without_counter": sum(row["status"] == "UNKNOWN" and row["counters"] == 0
                                        for row in trades.values()),
        "note": "This older log cohort is separate from SQLite and was not randomized; UNKNOWN is not a failed deal.",
    }


def report(recorder: PricingRecorder, legacy_log: Path | None = None,
           legacy_first: int = 600) -> dict:
    rows = recorder.all_trades()
    items = sorted({row["item"] for row in rows})
    arms = sorted({(row["item"], row["assigned_arm"]) for row in rows})
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(recorder.path),
        "cohort": "new SQLite-recorded tasks only; historical log tasks are excluded",
        "ledger_cursor": recorder.ledger_cursor(),
        "overall": summarize(rows),
        "by_item": {item: summarize([row for row in rows if row["item"] == item]) for item in items},
        "by_item_arm": {f"{item}/{arm}": summarize(
            [row for row in rows if row["item"] == item and row["assigned_arm"] == arm])
            for item, arm in arms},
        "ledger_lag": ledger_lag(recorder.path),
        "notes": [
            "Negotiation and ledger status are independent; their counts can overlap.",
            "Rates show both recorded-attempt and actual-exposure denominators; unknowns are retained.",
            "A seller-emitted ask is not proof the buyer received it; verify with A2A response logs before a price experiment.",
        ],
    }
    if legacy_log is not None:
        result["legacy_log_cohort"] = legacy_log_summary(legacy_log, legacy_first)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="state/pricing_state.sqlite")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--legacy-log", type=Path,
                        help="show the first historical seller attempts separately")
    parser.add_argument("--legacy-first", type=int, default=600)
    args = parser.parse_args()
    data = report(PricingRecorder(args.db), args.legacy_log, args.legacy_first)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for label, values in [("ALL", data["overall"]), *data["by_item"].items()]:
        print(label)
        for key, value in values.items():
            print(f"  {key}: {value}")
    print("ledger_lag", json.dumps(data["ledger_lag"], ensure_ascii=False))
    if "legacy_log_cohort" in data:
        print("legacy_log_cohort", json.dumps(data["legacy_log_cohort"], ensure_ascii=False))
    print("ledger_cursor", data["ledger_cursor"])


if __name__ == "__main__":
    main()
