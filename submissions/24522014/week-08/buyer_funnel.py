"""Read-only funnel: order -> selection -> quote -> offer -> receipt -> actual payment, with failure causes.

A2A "purchased" and a receipt are not payment. Paid means .market/payments.jsonl has paid:true with a ledger
for the same order, shop, item and price. Runs logged before decision reasons existed are labeled as such
instead of being assumed to be quota refusals.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from settlement_memory import payment_outcomes, reconcile

ROOT = Path(__file__).resolve().parent
STAGES = ("orders", "selected", "quoted", "offered", "receipt", "paid")
STAMP = "%Y%m%dT%H%M%S%fZ"


def stamp(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"Time needs an explicit offset: {value}")
    return moment.astimezone(timezone.utc).strftime(STAMP)


def load_runs(root, since, until=None):
    runs = []
    for path in sorted((Path(root) / "logs/market-buyer").glob("*.json")):
        name = path.name.split("-")[0]
        if name < since or (until and name >= until):
            continue
        runs.append((name, json.loads(path.read_text(encoding="utf-8"))["record"]))
    return runs


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def legacy_view(root, record):
    """What the raw A2A logs of a run without a decision trail can actually prove."""
    directory = Path(root) / "logs/buyer" / Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
    quoted = False
    for path in sorted(directory.glob("*.response.txt")) if record.get("buyer_log_dir") else ():
        reply = read_json(path)
        result = reply.get("result") if isinstance(reply, dict) else None
        task = result.get("task", result) if isinstance(result, dict) else None
        if isinstance(task, dict) and (task.get("status") or {}).get("state") == "TASK_STATE_INPUT_REQUIRED":
            quoted = True
            break
    summary = read_json(directory / "outcome.json") or {}
    negotiation = summary.get("outcome")
    cause = negotiation
    if negotiation == "walked_away":
        saved = read_json(directory / f"decision-{summary.get('turns', 0):02d}.json")
        if saved is None:
            cause = "walk_away_unlogged_pre_fix"
        elif saved.get("source") == "deterministic":
            cause = "rule_repeated_offer"
        else:
            cause = "model_walk_away" if saved.get("status") == "ok" else "model_failed"
    return quoted, cause


def payment_cause(row):
    reason = str(row.get("payment_reason", "unknown"))
    if "grade" in reason:
        return "payment_refused:grade"
    if "shelf life" in reason:
        return "payment_refused:shelf_life"
    if "warranty" in reason:
        return "payment_refused:warranty"
    if "certificate" in reason:
        return "payment_refused:certificate"
    return "payment_refused:" + re.sub(r"https?://\S+", "<url>", re.sub(r"\d[\d.,]*", "#", reason))[:80]


def classify_run(root, record, payments):
    """Return (stages reached, cause or 'paid') for one hook run."""
    reached = {"orders"}
    if record.get("outcome") == "research_skipped":
        return reached, "selection:" + str((record.get("buyer_support") or {}).get("status", "unknown"))
    reached.add("selected")
    trail = record.get("decision_trail")
    if trail is None:
        quoted, cause = legacy_view(root, record)
    else:
        quoted, cause = bool(trail), record.get("failure_reason")
    if quoted:
        reached.add("quoted")
    if record.get("agreed_price") is not None:
        reached.add("offered")
    if isinstance(record.get("receipt"), dict):
        reached.add("receipt")
    if cause in ("seller_rejected", "canceled"):
        cause += "_after_offer" if "offered" in reached else "_at_open" if "quoted" not in reached else "_at_quote"
    settled = reconcile([record], payments)[0]
    outcome = settled.get("outcome")
    if outcome == "purchased":
        if settled.get("verification") == "market_settled":
            reached.add("paid")
            return reached, "paid"
        return reached, "receipt_without_order_id"
    if outcome == "awaiting_payment":
        return reached, "receipt_no_payment_row"
    if outcome == "payment_refused":
        return reached, payment_cause(settled)
    if outcome == "payment_record_mismatch":
        return reached, outcome
    return reached, cause or outcome or "unknown"


def funnel(root, since, until=None, payments_path=None):
    root = Path(root)
    runs = load_runs(root, since, until)
    payments = payment_outcomes(payments_path or root / ".market/payments.jsonl")
    counts, causes, sources, parser_reasons = Counter(), Counter(), Counter(), Counter()
    filter_basis, conflicts, shelf_notes = Counter(), Counter(), Counter()
    counter_runs, counter_exclusions = Counter(), Counter()
    orders, instrumented = set(), 0
    for _, record in runs:
        reached, cause = classify_run(root, record, payments)
        counts.update(reached)
        causes[cause] += 1
        if record.get("order"):
            orders.add(record["order"])
        trail = record.get("decision_trail")
        if trail is not None:
            instrumented += 1
            if any(e["reason_code"] == "rule_counter_offer" for e in trail):
                counter_runs["offered"] += 1
                counter_runs["receipt"] += "receipt" in reached
                counter_runs["paid"] += "paid" in reached
            for entry in trail:
                sources[f"{entry['source']}:{entry['reason_code']}"] += 1
                excluded = (entry.get("counter_check") or {}).get("reason_code")
                if excluded and excluded != "rule_counter_offer":
                    counter_exclusions[excluded] += 1
                check = entry.get("quote_check")
                if check:
                    conflicts.update(check["conflicts"])
                    shelf_notes[check["text_hours_note"]] += 1
                if entry["source"] == "quote_filter":
                    filter_basis[f"{entry['reason_code']}|basis={check['basis']}"] += 1
                elif entry["source"] != "deterministic":
                    parser_reasons[f"{entry['reason_code']}|parser={entry['parser_reason']}"] += 1
    stages, previous = [], None
    for stage in STAGES:
        count = counts[stage]
        stages.append({"stage": stage, "count": count,
                       "of_previous": round(count / previous, 4) if previous else None,
                       "of_selected": round(count / counts["selected"], 4)
                       if counts["selected"] and stage != "orders" else None})
        previous = count
    return {"window": {"since": since, "until": until, "first_run": runs[0][0] if runs else None,
                       "last_run": runs[-1][0] if runs else None},
            "sample": {"hook_runs": len(runs), "distinct_orders": len(orders),
                       "runs_with_decision_trail": instrumented},
            "funnel": stages, "causes": dict(causes.most_common()),
            "decision_paths": dict(sources.most_common()),
            "non_rule_paths_by_parser_reason": dict(parser_reasons.most_common()),
            "counter_offer": {"offered": counter_runs["offered"], "receipt": counter_runs["receipt"],
                              "paid": counter_runs["paid"], "exclusions": dict(counter_exclusions.most_common())},
            "quote_filter_by_basis": dict(filter_basis.most_common()),
            "quote_conflicts": dict(conflicts.most_common()),
            "quote_text_shelf_life": dict(shelf_notes.most_common())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO time with offset, e.g. 2026-11-04T09:39:00Z")
    parser.add_argument("--until")
    args = parser.parse_args()
    report = funnel(ROOT, stamp(args.since), stamp(args.until) if args.until else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
