"""Small runtime stage events and an offline, observation-only JSONL report.

Examples::

    emit_stage(trace, "offer_accepted", side="seller", task_id=task_id,
               item="lamp", price=100, evidence="validated_decision")
    python trade_funnel.py logs/runtime/seller-*.jsonl --output logs/funnel.json

Input paths are explicit: historical legacy events are never guessed into stages.
Repeated stages count once per (side, task_id), even across restarted processes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any


PATHS = {
    "seller": ("negotiation_started", "quote_issued", "offer_received",
               "offer_accepted", "receipt_issued", "market_verified"),
    "buyer": ("negotiation_started", "quote_observed", "offer_sent",
              "receipt_matched", "market_verified"),
}
EVIDENCE = {"offer_accepted": "validated_decision", "receipt_issued": "local_signature",
            "receipt_matched": "receipt_contract", "market_verified": "market_kit_feedback"}
OUTCOMES = {"canceled", "rejected", "error", "unresolved", "completed", "verified"}
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def _event_fields(stage: str, *, side: str, task_id: str,
                  item: str | None = None, shop: str | None = None,
                  quote_id: str | None = None, price: int | float | None = None,
                  quantity: int | None = None, offer_number: int | None = None,
                  outcome: str | None = None, reason_code: str | None = None,
                  elapsed_ms: int | float | None = None,
                  evidence: str | None = None) -> dict:
    if side not in PATHS or stage not in (*PATHS.get(side, ()), "ended"):
        raise ValueError("unsupported trade stage or side")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
        raise ValueError("task_id must be a bounded nonempty identifier")
    if stage in EVIDENCE and evidence != EVIDENCE[stage]:
        raise ValueError(f"{stage} requires {EVIDENCE[stage]} evidence")
    if stage == "ended" and outcome not in OUTCOMES - {"verified"}:
        raise ValueError("ended requires a factual local outcome")
    if stage == "market_verified" and outcome != "verified":
        raise ValueError("market_verified requires authoritative verified outcome")
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError("unsupported outcome")
    if reason_code is not None and (not isinstance(reason_code, str) or not _CODE.fullmatch(reason_code)):
        raise ValueError("reason_code must be a short code, not peer text")
    for name, value in (("item", item), ("shop", shop), ("quote_id", quote_id)):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise ValueError(f"invalid {name}")
    for name, value in (("price", price), ("elapsed_ms", elapsed_ms)):
        if value is None:
            continue
        try:
            valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"invalid {name}")
    for name, value in (("quantity", quantity), ("offer_number", offer_number)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError(f"invalid {name}")
    fields = {"stage": stage, "side": side, "task_id": task_id, "item": item,
              "shop": shop, "quote_id": quote_id, "price": price, "quantity": quantity,
              "offer_number": offer_number, "outcome": outcome, "reason_code": reason_code,
              "elapsed_ms": elapsed_ms, "evidence": evidence}
    # Feedback is observed on ingestion; it is not the original settlement time.
    if stage == "market_verified":
        fields["time_basis"] = "feedback_ingested"
    return {key: value for key, value in fields.items() if value is not None}


def emit_stage(trace: Any, stage: str, *, side: str, task_id: str,
               item: str | None = None, shop: str | None = None,
               quote_id: str | None = None, price: int | float | None = None,
               quantity: int | None = None, offer_number: int | None = None,
               outcome: str | None = None, reason_code: str | None = None,
               elapsed_ms: int | float | None = None, evidence: str | None = None) -> bool:
    """Emit a bounded event; callers must supply actual validated observations.

    ``receipt_matched`` describes contract matching, not signature verification.
    ``receipt_issued`` describes a local signature, not a counted Market deal.
    Only authoritative kit feedback permits ``market_verified``. Raw responses,
    private prices, keys, and arbitrary exception messages are not accepted fields.
    Invalid optional observations and trace I/O failures return False so diagnostics
    do not interrupt a valid trade. Wrong stage/side or missing evidence still raises.
    """
    if side not in PATHS or stage not in (*PATHS.get(side, ()), "ended"):
        raise ValueError("unsupported trade stage or side")
    if stage in EVIDENCE and evidence != EVIDENCE[stage]:
        raise ValueError(f"{stage} requires {EVIDENCE[stage]} evidence")
    try:
        fields = _event_fields(stage, side=side, task_id=task_id, item=item, shop=shop,
                               quote_id=quote_id, price=price, quantity=quantity,
                               offer_number=offer_number, outcome=outcome,
                               reason_code=reason_code, elapsed_ms=elapsed_ms, evidence=evidence)
    except ValueError:
        try:
            trace.write("trade_stage_unavailable", side=side, stage=stage, reason_code="invalid_observation")
        except OSError:
            pass
        return False
    try:
        trace.write("trade_stage", **fields)
    except OSError:
        return False
    return True


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


class Funnel:
    """Bounded task state; counters describe observations, never inferred stages."""

    def __init__(self, *, max_tasks: int = 100_000):
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        self.max_tasks = max_tasks
        self.tasks: dict[tuple[str, str], dict] = {}
        self.counts: Counter = Counter()

    def observe(self, record: Any) -> None:
        if not isinstance(record, dict) or record.get("event") != "trade_stage":
            self.counts["non_stage_records"] += 1
            return
        names = ("side", "task_id", "item", "shop", "quote_id", "price", "quantity",
                 "offer_number", "outcome", "reason_code", "elapsed_ms", "evidence")
        try:
            fields = _event_fields(record.get("stage"), **{n: record[n] for n in names if n in record})
        except (TypeError, ValueError):
            self.counts["invalid_stage_records"] += 1
            return
        key = (fields["side"], fields["task_id"])
        if key not in self.tasks:
            if len(self.tasks) >= self.max_tasks:
                raise ValueError("task limit reached; narrow the explicit input window or raise --max-tasks")
            self.tasks[key] = {"stages": {}, "outcomes": set(), "reasons": set(), "offer_number": 0}
        task = self.tasks[key]
        stage = fields["stage"]
        observed_at = None if stage == "market_verified" else _timestamp(record.get("time"))
        if stage in task["stages"]:
            self.counts["repeat_stage_observations"] += 1
            previous = task["stages"][stage]
            if observed_at is not None and (previous is None or observed_at < previous):
                task["stages"][stage] = observed_at
        else:
            task["stages"][stage] = observed_at
        if stage == "ended":
            task["outcomes"].add(fields["outcome"])
            if fields.get("reason_code"):
                if len(task["reasons"]) < 64 or fields["reason_code"] in task["reasons"]:
                    task["reasons"].add(fields["reason_code"])
                else:
                    self.counts["reason_details_truncated"] += 1
        task["offer_number"] = max(task["offer_number"], fields.get("offer_number", 0))
        self.counts["valid_stage_records"] += 1

    def report(self, *, capture_completeness: str = "unknown") -> dict:
        if capture_completeness not in {"unknown", "partial", "complete"}:
            raise ValueError("invalid capture completeness")
        sides = {}
        for side, path in PATHS.items():
            tasks = [task for (task_side, _), task in self.tasks.items() if task_side == side]
            stage_counts, statuses, pending, failures, unresolved, reasons = (Counter() for _ in range(6))
            transitions = {(left, right): {"both_observed_tasks": 0, "seconds": [],
                                           "backwards_timestamp_tasks": 0}
                           for left, right in zip(path, path[1:])}
            missing = Counter()
            for task in tasks:
                stages, outcomes = task["stages"], task["outcomes"]
                stage_counts.update(stages.keys())
                latest = next((stage for stage in reversed(path) if stage in stages), "unknown")
                if "market_verified" in stages:
                    status = "market_verified"
                elif outcomes & {"error", "rejected", "canceled"}:
                    status = "explicit_failure"
                    failures[latest] += 1
                elif "unresolved" in outcomes:
                    status = "explicitly_unresolved"
                    unresolved[latest] += 1
                else:
                    status = "right_censored_pending"
                    pending[latest] += 1
                statuses[status] += 1
                reasons.update(task["reasons"])
                for stage in path:
                    if stage not in stages:
                        missing[stage] += 1
                for (left, right), data in transitions.items():
                    if left not in stages or right not in stages:
                        continue
                    data["both_observed_tasks"] += 1
                    # Never time settlement from delayed imported feedback.
                    if right == "market_verified":
                        continue
                    before, after = stages[left], stages[right]
                    if before is not None and after is not None:
                        if after >= before:
                            data["seconds"].append(after - before)
                        else:
                            data["backwards_timestamp_tasks"] += 1
            paired = []
            for (left, right), data in transitions.items():
                seconds = data.pop("seconds")
                paired.append({"from": left, "to": right, "from_stage_tasks": stage_counts[left],
                               **data, "timed_tasks": len(seconds),
                               "mean_observed_seconds": sum(seconds) / len(seconds) if seconds else None,
                               "max_observed_seconds": max(seconds) if seconds else None})
            sides[side] = {"observed_tasks": len(tasks), "stage_tasks": dict(stage_counts),
                           "status_tasks": dict(statuses), "pending_by_last_observed_stage": dict(pending),
                           "failure_by_last_observed_stage": dict(failures),
                           "unresolved_by_last_observed_stage": dict(unresolved),
                           "ended_reason_tasks": dict(reasons),
                           "missing_stage_observations": dict(missing), "paired_observations": paired,
                           "tasks_with_conflicting_local_outcomes": sum(len(t["outcomes"]) > 1 for t in tasks)}
        return {
            "schema_version": 1, "capture_completeness": capture_completeness,
            "observation_coverage": "instrumented trade_stage events in the explicit input files only",
            "notes": ["Denominators are distinct observed (side, task_id) tasks, not visits or all Market orders.",
                      "Missing observations do not prove a stage did not happen; no preceding stages are inferred.",
                      "Pending tasks are right-censored at capture end, not inferred abandonment or failure.",
                      "Local receipt issue/matching and local completed outcomes do not establish Market verification.",
                      "Market feedback timestamps are ingestion times and excluded from elapsed durations.",
                      "Durations pair earliest observed stage timestamps; repeated quote/offer turns may be interleaved.",
                      "These counts locate observed gaps and explicit failures; they do not establish causes."],
            "records": dict(self.counts), "sides": sides,
        }


def analyze_paths(paths: list[str | Path], *, capture_completeness: str = "unknown",
                  max_tasks: int = 100_000, max_line_bytes: int = 1_048_576) -> dict:
    """Read JSONL a line at a time with bounded line and task memory."""
    if max_line_bytes < 1:
        raise ValueError("max_line_bytes must be positive")
    funnel = Funnel(max_tasks=max_tasks)
    sources = []
    for value in paths:
        path = Path(value)
        stats = Counter()
        with path.open("rb") as stream:
            while True:
                raw = stream.readline(max_line_bytes + 1)
                if not raw:
                    break
                stats["lines"] += 1
                if len(raw) > max_line_bytes:
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(max_line_bytes + 1)
                    stats["oversized_lines_skipped"] += 1
                    continue
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    stats["invalid_json_lines"] += 1
                    continue
                funnel.observe(record)
        sources.append({"path": str(path), "capture_completeness": capture_completeness, **stats})
    report = funnel.report(capture_completeness=capture_completeness)
    report["sources"] = sources
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="explicit JSONL trace paths; shell globs are permitted")
    parser.add_argument("--capture-completeness", choices=("unknown", "partial", "complete"), default="unknown",
                        help="operator assertion about source capture, not proof that every task finished")
    parser.add_argument("--max-tasks", type=int, default=100_000)
    parser.add_argument("--max-line-bytes", type=int, default=1_048_576)
    parser.add_argument("--output", type=Path, help="write a NEW JSON report; existing files are preserved")
    args = parser.parse_args()
    try:
        report = analyze_paths(args.inputs, capture_completeness=args.capture_completeness,
                               max_tasks=args.max_tasks, max_line_bytes=args.max_line_bytes)
        rendered = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        else:
            print(rendered, end="")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"trade_funnel: {exc}\n")


if __name__ == "__main__":
    main()
