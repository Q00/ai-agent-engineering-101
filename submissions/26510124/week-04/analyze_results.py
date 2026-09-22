#!/usr/bin/env python3
"""Independently audit saved experiment records and print report-ready Markdown.

Uses only the standard library and never calls a model or changes experiment
files. State is replayed from logged parse results, not imported from the runner.
Diagnostic text matches are evidence candidates, not automatic semantic labels.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

CONDITIONS = ("free", "tagged", "structured")
ACTS = {"propose", "accept-proposal", "reject-proposal", "refuse"}
HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]
MEASUREMENTS = ("outcome", "price", "correct", "violation", "turns", "format_errors", "reader_calls")
TAG = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s+")
ROOT = Path(__file__).resolve().parent


class AuditError(ValueError):
    """Saved records disagree with each other or the experiment contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def cell(value) -> str:
    return "" if value is None else str(value)


def key(row: dict) -> tuple[str, str, str]:
    return str(row["condition"]), str(row["run"]), str(row["scenario"])


def location(event: dict) -> str:
    return f"{event['_file']}:{event['_line']}"


def load_log(path: Path) -> tuple[list[dict], list[str]]:
    events, fragments = [], []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, raw in enumerate(lines, 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            recovered = False
            if line_number < len(lines):
                try:
                    recovered = json.loads(lines[line_number]).get("event") == "log_recovery"
                except (json.JSONDecodeError, AttributeError):
                    pass
            require(recovered or line_number == len(lines), f"{path.name}:{line_number}: invalid JSONL")
            fragments.append(f"logs/{path.name}:{line_number}: preserved partial log line")
            continue
        require(isinstance(event, dict), f"{path.name}:{line_number}: event must be an object")
        events.append({**event, "_file": f"logs/{path.name}", "_line": line_number})
    return events, fragments


def replay(row: dict, scenario: dict, events: list[dict], max_turns: int) -> dict:
    label = "/".join(key(row))
    condition = row["condition"]
    require(sum(e.get("event") == "episode_begin" for e in events) == 1,
            f"{label}: expected one episode_begin (an episode must not be replayed)")
    records = [e for e in events if e.get("event") == "episode_record"]
    require(len(records) == 1, f"{label}: expected one durable episode_record")
    saved_row = records[0].get("row", {})
    for field in HEADER:
        require(cell(saved_row.get(field)) == row[field], f"{label}: CSV/{field} differs from episode_record")
    results = [e for e in events if e.get("event") == "episode_result"]
    require(len(results) <= 1, f"{label}: duplicate episode_result")
    completed = bool(row["outcome"])
    require(not completed or len(results) == 1, f"{label}: completed CSV row has no episode_result")
    if not completed:
        require(bool(row["note"]), f"{label}: crash has no note")
        require(all(row[field] == "" for field in ("price", "correct", "violation")),
                f"{label}: crash must not fabricate outcome metrics")
        require(bool(results) or records[0].get("recovered_crash"), f"{label}: unexplained crash row")

    last_price = {"buyer": None, "seller": None}
    outcome, price = "open", None
    turns = format_errors = 0
    calls = Counter(agent=0, reader=0)
    logical = Counter(agent=0, reader=0)
    parsed_turns, reader_turns = set(), Counter()
    diagnostics, evidence, warnings = Counter(), [], []
    messages, reader_raw = {}, {}
    expected_transition = None
    pending_call = None
    errors = []

    def note(category: str, event: dict, raw: str = "") -> None:
        diagnostics[category] += 1
        turn = event.get("turn")
        evidence.append({
            "category": category, "episode": label, "source": location(event), "raw": raw,
            "message_source": location(messages[turn]) if turn in messages else "",
            "reader_source": location(reader_raw[turn]) if turn in reader_raw else "",
        })

    for event in events:
        kind = event.get("event")
        where = location(event)
        if kind == "episode_begin":
            require(event.get("scenario_config") == scenario, f"{where}: scenario differs from frozen set")
        elif kind == "episode_start":
            require(event.get("reserve") == scenario["reserve"] and event.get("budget") == scenario["budget"],
                    f"{where}: private limits differ from frozen set")
            require(event.get("max_turns") == max_turns, f"{where}: max_turns differs from experiment")
        elif kind == "model_call":
            require(pending_call is None, f"{where}: model call overlaps an unfinished call")
            call_kind = event.get("kind")
            require(call_kind in {"agent", "reader"}, f"{where}: unknown model-call kind")
            pending_call = {"kind": call_kind, "retries": 0}
            calls[call_kind] += 1
            logical[call_kind] += 1
            if call_kind == "reader":
                reader_turns[event.get("turn")] += 1
        elif kind == "model_retry":
            require(pending_call is not None, f"{where}: retry without a model call")
            pending_call["retries"] += 1
            require(event.get("retry") == pending_call["retries"], f"{where}: retry sequence mismatch")
            calls[pending_call["kind"]] += 1
        elif kind == "model_result":
            require(pending_call is not None, f"{where}: model result without a call")
            require(event.get("kind") == pending_call["kind"], f"{where}: model result kind mismatch")
            require(event.get("retries") == pending_call["retries"], f"{where}: retry events/result mismatch")
            pending_call = None
        elif kind == "message":
            require(outcome == "open", f"{where}: message generated after terminal state")
            require(expected_transition is None, f"{where}: prior parse has no transition")
            turns += 1
            require(event.get("turn") == turns, f"{where}: message turn is not sequential")
            require(event.get("actor") == ("buyer" if turns % 2 else "seller"), f"{where}: actors do not alternate")
            require(isinstance(event.get("raw"), str), f"{where}: raw message missing")
            messages[turns] = event
        elif kind == "reader_result":
            require(event.get("turn") in messages, f"{where}: reader has no delivered message")
            require(event.get("turn") not in reader_raw, f"{where}: duplicate reader response")
            reader_raw[event["turn"]] = event
        elif kind == "parse_result":
            turn = event.get("turn")
            require(turn == turns and turn in messages and turn not in parsed_turns, f"{where}: parse/message mismatch")
            require(type(event.get("ok")) is bool, f"{where}: parser ok must be boolean")
            actor = messages[turn]["actor"]
            require(event.get("actor") == actor, f"{where}: parse actor mismatch")
            other = "seller" if actor == "buyer" else "buyer"
            raw = messages[turn]["raw"]
            tag = TAG.match(raw) if condition == "tagged" else None
            needs_reader = condition == "free" or (tag is not None and tag.group(1) == "propose")
            require(reader_turns[turn] == int(needs_reader), f"{where}: wrong logical reader call count for format")
            require((turn in reader_raw) == needs_reader, f"{where}: raw reader response missing or unexpected")
            parsed_turns.add(turn)
            before = {"last_price": dict(last_price), "outcome": outcome, "price": price}
            if not event["ok"]:
                format_errors += 1
                reason = "parse_failure_continue"
                note("format_error", event, raw)
            else:
                act = event.get("performative")
                require(act in ACTS, f"{where}: unknown parsed performative")
                if condition == "tagged":
                    require(tag is not None and tag.group(1) == act, f"{where}: parser changed explicit tag")
                if act == "propose":
                    amount = event.get("price")
                    require(type(amount) is int and amount >= 0, f"{where}: invalid parsed proposal price")
                    last_price[actor] = amount
                    reason = "proposal_recorded"
                elif act == "accept-proposal":
                    if last_price[other] is None:
                        reason = "accept_without_opponent_proposal"
                        note("accept_without_offer", event, raw)
                    else:
                        outcome, price = "deal", last_price[other]
                        reason = "opponent_proposal_accepted"
                elif act == "reject-proposal":
                    reason = "rejection_continue"
                    if condition == "tagged" and re.search(r"\d", raw):
                        note("tagged_reject_with_number_candidate", event, raw)
                else:
                    outcome, reason = "no_deal", "refusal_ends_negotiation"
                    if condition == "free" and "?" in raw:
                        note("free_question_labeled_refuse_candidate", event, raw)
                if condition == "free" and act == "propose" and len(re.findall(r"\d+", raw)) >= 2:
                    note("free_multiple_numbers_candidate", event, raw)
            if condition == "structured" and event.get("trailing", "").strip():
                note("structured_suffix", event, raw)
            expected_transition = {
                "before": before, "after": {"last_price": dict(last_price), "outcome": outcome, "price": price},
                "reason": reason,
            }
        elif kind == "transition":
            require(expected_transition is not None, f"{where}: transition without a parse")
            for field, value in expected_transition.items():
                require(event.get(field) == value, f"{where}: independently replayed {field} differs")
            expected_transition = None
        elif kind == "episode_error":
            errors.append(event)

    require(turns <= max_turns, f"{label}: turn limit exceeded")
    if condition == "structured":
        require(calls["reader"] == 0, f"{label}: structured made reader calls")
    if completed:
        require(not errors and pending_call is None, f"{label}: completed row has failed/pending call")
        require(len(parsed_turns) == turns and expected_transition is None, f"{label}: incomplete protocol processing")
        require(outcome != "open" or turns == max_turns, f"{label}: open before turn limit")
        require(logical["agent"] == turns, f"{label}: agent calls/messages mismatch")
    elif errors and errors[-1].get("type") == "KeyboardInterrupt" and pending_call and pending_call["retries"]:
        # model_retry is logged before sleeping. A Ctrl-C during that sleep can
        # leave the next attempt merely scheduled, not actually sent.
        warnings.append(f"{label}: interrupted retry; actual outstanding attempt count is unknown")
        calls[pending_call["kind"]] = None

    possible = int(scenario["reserve"] <= scenario["budget"])
    violation = int(outcome == "deal" and not scenario["reserve"] <= price <= scenario["budget"])
    correct = int((outcome == "deal" and possible and not violation) or (outcome == "no_deal" and not possible))
    expected = {
        "deal_possible": possible, "turns": turns, "format_errors": format_errors,
        "reader_calls": calls["reader"], "outcome": outcome if completed else "",
        "price": price if completed else "", "correct": correct if completed else "",
        "violation": violation if completed else "",
    }
    for field, value in expected.items():
        if field in {"turns", "format_errors", "reader_calls"} and not completed and row[field] == "":
            continue  # an interrupted process supplies partial observations only
        if value is not None or field == "price":
            require(row[field] == cell(value), f"{label}: {field}={row[field]!r}, replay expects {cell(value)!r}")
    if results:
        result = results[0]
        require(result.get("status") == ("completed" if completed else "crashed"), f"{label}: result status mismatch")
        for field in MEASUREMENTS:
            require(cell(result.get(field)) == row[field], f"{label}: result/{field} differs from CSV")
        require(result.get("note") == row["note"], f"{label}: result note differs from CSV")
        if calls["agent"] is not None:
            require(result.get("metrics", {}).get("agent_calls") == calls["agent"], f"{label}: agent calls differ from log")
    if completed and violation:
        note("limit_violation", records[0], f"recorded price={price}, reserve={scenario['reserve']}, budget={scenario['budget']}")
    if completed and outcome == "open":
        note("open_at_turn_limit", records[0])
    if completed and outcome == "no_deal" and turns == 1:
        note("first_turn_no_deal", records[0], messages[1]["raw"])
    return {"diagnostics": diagnostics, "evidence": evidence, "warnings": warnings}


def audit(root: Path, runs: int = 3) -> dict:
    root = Path(root)
    require(runs > 0, "runs must be positive")
    manifest_path = root / "experiment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    configuration = manifest.get("configuration", {})
    scenario_path = root / "scenarios.json"
    scenarios = (json.loads(scenario_path.read_text(encoding="utf-8")) if scenario_path.exists()
                 else configuration.get("scenarios"))
    require(isinstance(scenarios, list) and len(scenarios) >= 4, "four or more frozen scenarios required")
    by_id = {str(s["id"]): s for s in scenarios}
    require(len(by_id) == len(scenarios), "duplicate scenario ids")
    require(not configuration or configuration.get("scenarios") == scenarios, "scenarios differ from experiment manifest")
    max_turns = configuration.get("max_turns", 8)
    with (root / "results.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames == HEADER, "CSV header differs from required contract")
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows), "malformed CSV row")
    keys = [key(row) for row in rows]
    require(len(keys) == len(set(keys)), "duplicate CSV episode key")
    expected = {(c, str(r), sid) for c in CONDITIONS for r in range(1, runs + 1) for sid in by_id}
    require(set(keys) == expected, f"episode coverage mismatch: missing={sorted(expected - set(keys))}, extra={sorted(set(keys) - expected)}")
    expected_logs = {f"{c}-run-{r:02d}.log" for c in CONDITIONS for r in range(1, runs + 1)}
    actual_logs = {p.name for p in (root / "logs").glob("*.log")}
    require(actual_logs == expected_logs, f"run-log coverage mismatch: missing={sorted(expected_logs - actual_logs)}, extra={sorted(actual_logs - expected_logs)}")
    all_events, warnings = {}, []
    for condition in CONDITIONS:
        for run in range(1, runs + 1):
            events, fragments = load_log(root / "logs" / f"{condition}-run-{run:02d}.log")
            warnings.extend(fragments)
            configs = [e for e in events if e.get("event") == "run_config"]
            require(len(configs) == 1, f"{condition}/{run}: expected one run_config")
            require(configs[0].get("max_turns") == max_turns, f"{condition}/{run}: max_turns mismatch")
            for event in events:
                if event.get("event") == "log_recovery":
                    continue
                require(event.get("condition") == condition and str(event.get("run")) == str(run), f"{location(event)}: run context mismatch")
                if "scenario" in event:
                    require(str(event["scenario"]) in by_id, f"{location(event)}: unknown scenario")
                if manifest and event.get("event") in {"run_config", "run_resume"}:
                    require(event.get("fingerprint") == manifest["fingerprint"], f"{location(event)}: fingerprint mismatch")
            all_events[(condition, str(run))] = events
    diagnostics, evidence = Counter(), []
    for row in rows:
        require(row["outcome"] in {"deal", "no_deal", "open", ""}, "unknown outcome")
        for field in ("deal_possible", "correct", "violation"):
            require(row[field] in {"0", "1", ""}, f"{key(row)}: {field} must be 0/1 or unknown")
        for field in ("price", "turns", "format_errors", "reader_calls"):
            require(row[field] == "" or row[field].isdigit(), f"{key(row)}: {field} must be nonnegative integer or unknown")
        events = [e for e in all_events[(row["condition"], row["run"])] if str(e.get("scenario")) == row["scenario"]]
        findings = replay(row, by_id[row["scenario"]], events, max_turns)
        diagnostics.update(findings["diagnostics"])
        evidence.extend(findings["evidence"])
        warnings.extend(findings["warnings"])
    return {"rows": rows, "diagnostics": diagnostics, "evidence": evidence,
            "warnings": warnings, "logs": len(expected_logs), "scenarios": scenarios}


def markdown(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>").replace("\r", "") if value != "" else "—"


def aggregate(rows: list[dict], field: str, *, mean: bool = False) -> str:
    known = [int(row[field]) for row in rows if row[field] != ""]
    unknown = len(rows) - len(known)
    if not known:
        return f"unknown ({unknown} episodes)"
    result = f"{sum(known) / len(known):.2f}" if mean else str(sum(known))
    return f"{result} (+{unknown} unknown)" if unknown else result


def render_report(report: dict, evidence_limit: int = 12, category: str | None = None) -> str:
    rows = report["rows"]
    lines = [f"Audit passed: {len(rows)} episode rows, {report['logs']} run logs; states and CSV agree.", "",
             "| condition | correct / all | deal / no_deal / open / crashed | violations | mean turns | format errors | reader calls |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for condition in CONDITIONS:
        group = [row for row in rows if row["condition"] == condition]
        known_correct = sum(int(row["correct"]) for row in group if row["correct"] != "")
        unknown = sum(row["correct"] == "" for row in group)
        correct = (f"unknown ({unknown} episodes)" if unknown == len(group) else
                   f"{known_correct}/{len(group)} ({100 * known_correct / len(group):.1f}%)")
        if unknown and unknown != len(group):
            correct += f"; {unknown} unknown"
        outcomes = Counter(row["outcome"] for row in group)
        distribution = " / ".join(str(outcomes[value]) for value in ("deal", "no_deal", "open", ""))
        lines.append("| " + " | ".join([condition, correct, distribution, aggregate(group, "violation"),
                     aggregate(group, "turns", mean=True), aggregate(group, "format_errors"), aggregate(group, "reader_calls")]) + " |")
    lines.extend(["", "Unknown crash metrics remain unknown; means use only known values. Correct/all is the observed-success fraction, with unknown cases shown separately.", "",
                  "| " + " | ".join(HEADER) + " |", "| " + " | ".join("---" for _ in HEADER) + " |"])
    for row in rows:
        lines.append("| " + " | ".join(markdown(row[field]) for field in HEADER) + " |")
    lines.extend(["", "Diagnostics (text matches are candidates for human interpretation):", ""])
    categories = (
        "accept_without_offer", "structured_suffix", "free_question_labeled_refuse_candidate",
        "free_multiple_numbers_candidate", "tagged_reject_with_number_candidate",
        "format_error", "limit_violation", "open_at_turn_limit", "first_turn_no_deal",
    )
    lines.extend(f"- {name}: {report['diagnostics'][name]}" for name in categories)
    lines.extend(["", "Evidence candidates:", ""])
    selected = [e for e in report["evidence"] if category is None or e["category"] == category]
    for entry in selected[:evidence_limit]:
        raw = entry["raw"].replace("\n", " ")
        if len(raw) > 240:
            raw = raw[:237] + "..."
        related = "; ".join(f"{name.removesuffix('_source')}={entry[name]}"
                            for name in ("message_source", "reader_source") if entry[name])
        source = entry["source"] + (f" ({related})" if related else "")
        lines.append(f"- {source} [{entry['episode']}; {entry['category']}]: {raw}")
    if not selected:
        lines.append("- None observed for this selection.")
    for warning in report["warnings"]:
        lines.append(f"- Audit caveat: {warning}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="directory with results.csv and logs/")
    parser.add_argument("--runs", type=int, default=3, help="expected repeats per condition")
    parser.add_argument("--evidence-limit", type=int, default=12)
    parser.add_argument("--category", help="show evidence for one diagnostic category")
    args = parser.parse_args()
    try:
        report = audit(args.root, args.runs)
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    print(render_report(report, max(0, args.evidence_limit), args.category))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
