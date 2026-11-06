"""Compare Seller traces with SQLite; optionally backfill evidenced completions."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pricing import PricingRecorder


def seller_traces(path: Path) -> tuple[dict[str, list[dict]], int]:
    by_task: dict[str, list[dict]] = defaultdict(list)
    record_errors = 0
    with path.open(encoding="utf-8", errors="replace") as source:
        lines = source.readlines()
    for line_number, line in enumerate(lines, 1):
        if "[seller] {" not in line:
            continue
        try:
            event = json.loads(line.split("[seller] ", 1)[1])
        except (ValueError, IndexError):
            continue
        if event.get("event") == "pricing_record_error":
            record_errors += 1
        task_id = event.get("task_id")
        if isinstance(task_id, str):
            by_task[task_id].append({**event, "log_line": line_number})
    return by_task, record_errors


def audit(recorder: PricingRecorder, log_path: Path, settle_seconds: int = 60,
          apply_completions: bool = False) -> dict:
    traces, errors = seller_traces(log_path)
    cutoff = datetime.now(timezone.utc).timestamp() - settle_seconds
    counts = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for trade in recorder.all_trades():
        if datetime.fromisoformat(trade["last_activity_at"]).timestamp() > cutoff:
            counts["recent_in_flight_excluded"] += 1
            continue
        counts["settled_tasks_checked"] += 1
        task_id = trade["task_id"]
        logged = traces.get(task_id, [])
        events = recorder.events_for(task_id)
        opens = [event for event in logged if event["event"] == "seller_open"]
        counters = [event for event in logged if event["event"] == "seller_counter"]
        deals = [event for event in logged if event["event"] == "seller_deal"]
        rejects = [event for event in logged if event["event"] == "seller_reject"]
        db_counters = [event["data"]["ask"] for event in events if event["kind"] == "counter_sent"]
        db_offers = [event["data"]["offer"] for event in events if event["kind"] == "offer_received"]
        logged_answers = [event for event in logged
                          if event["event"] in {"seller_counter", "seller_deal", "seller_reject"}]
        logged_offers = [event.get("offer", event.get("price")) for event in logged_answers]

        def flag(name: str) -> None:
            counts[name] += 1
            if len(examples[name]) < 5:
                examples[name].append(task_id)

        if not opens:
            flag("missing_open_trace")
        elif trade["opening_ask_actual"] != opens[-1]["ask"]:
            flag("opening_ask_mismatch_or_unsent")
        else:
            counts["opening_ask_matched"] += 1
        if db_counters != [event["ask"] for event in counters]:
            flag("counter_sequence_mismatch")
        else:
            counts["counter_sequence_matched"] += 1
        if db_offers != logged_offers:
            if db_offers[:len(logged_offers)] == logged_offers:
                flag("buyer_offer_without_seller_answer")
            else:
                flag("buyer_offer_sequence_mismatch")
        else:
            counts["buyer_offer_sequence_matched"] += 1
        if deals and not trade["receipt_issued"]:
            flag("deal_trace_without_local_receipt")
        if trade["receipt_issued"] and not deals:
            flag("local_receipt_without_deal_trace")
        if rejects and trade["negotiation_status"] != "REJECTED":
            flag("reject_status_mismatch")
        if not trade["last_activity_at"]:
            flag("missing_last_activity")
        if deals and trade["receipt_issued"] and trade["final_price"] == deals[-1]["price"]:
            counts["receipt_price_matched"] += 1
            if apply_completions and recorder.confirm_completed_from_log(
                    task_id, deals[-1]["price"], f"seller_deal log line {deals[-1]['log_line']}"):
                counts["completions_backfilled"] += 1
    return {"source_log": str(log_path), "record_errors_in_log": errors,
            "counts": dict(counts), "examples": dict(examples),
            "note": "Only seller-emitted events are observed here; a client-side A2A response confirms delivery."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="state/pricing_state.sqlite")
    parser.add_argument("--log", type=Path, default=Path("logs/market-kit.log"))
    parser.add_argument("--settle-seconds", type=int, default=60)
    parser.add_argument("--apply-completions", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(PricingRecorder(args.db), args.log,
                           args.settle_seconds, args.apply_completions),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
