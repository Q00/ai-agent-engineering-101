"""Summarize saved benchmark evidence without executing agents or changing inputs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics


CLAIM_LIMITS = [
    "Observed runs only; no ranking, statistical dominance, or historical-provider reproduction claim.",
    "Native and controlled rehosting, different runs, items, budgets, and runner versions remain separate.",
    "Checkpointing runs and explicitly synthetic/stub results are excluded from outcome aggregates.",
    "Model request counts are evidence counts, not billed tokens or guaranteed provider invocation counts.",
    "Shared HTTP transport calls are global observations and are never attributed to individual matches.",
]


def _finite(value, *, positive=False, nonnegative=True):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and (value > 0 if positive else value >= 0 if nonnegative else True)
    except OverflowError:
        return False


def _constant(value):
    raise ValueError(f"non-JSON numeric value {value}")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=_constant, parse_float=_float)


def _synthetic(record):
    if not isinstance(record, dict):
        return False
    if any(record.get(key) is True for key in ("synthetic", "simulated", "test_only")):
        return True
    for key in ("mode", "test_mode", "evaluation_mode"):
        value = str(record.get(key, "")).lower()
        if any(word in value for word in ("synthetic", "simulat", "stub", "surrogate", "deterministic_guard")):
            return True
    return False


def _mean(values):
    return statistics.fmean(values) if values else None


def _observed_mean(rows, key):
    values = [row[key] for row in rows if _finite(row.get(key))]
    return {"mean": _mean(values), "observed": len(values), "missing": len(rows) - len(values)}


def _surplus(rows, role):
    # A failed attempt contributes zero only when its saved evidence explicitly says so.
    key = role + "_surplus"
    values = [row[key] for row in rows if _finite(row.get(key), nonnegative=False)]
    return {"per_attempt": _mean(values) if len(values) == len(rows) else None,
            "observed_mean": _mean(values), "known_attempts": len(values),
            "missing_attempts": len(rows) - len(values)}


def _trace_counts(match_dir, buyer, seller):
    counts = {role: {"own_harness_requests": None, "adapter_cli_requests": None,
                     "adapter_cli_processes": None, "adapter_cli_completed": None,
                     "request_count": None, "source": "unknown"} for role in ("buyer", "seller")}
    errors = []
    for role, dirname in (("buyer", "buyer-trace"), ("seller", "agent-trace")):
        traces = list((match_dir / dirname).glob("*.jsonl"))
        if traces:
            total = 0
            readable = True
            for path in traces:
                try:
                    for line in path.read_text(encoding="utf-8").splitlines():
                        event = json.loads(line, parse_constant=_constant, parse_float=_float)
                        if event.get("event") == "harness_request" and event.get("role", role) == role:
                            total += 1
                except (OSError, ValueError, AttributeError) as exc:
                    readable = False
                    errors.append(f"{path}: {type(exc).__name__}")
            if readable:
                counts[role]["own_harness_requests"] = total
                counts[role].update(request_count=total, source="own_harness_request_events")
    for role, dirname in (("buyer", "buyer-transport"), ("seller", "transport")):
        requests = []
        for path in (match_dir / dirname).glob("*/request.json"):
            try:
                metadata = _read(path)
                if not _synthetic(metadata) and str(metadata.get("source", "")).endswith("-cli"):
                    requests.append(path)
            except (OSError, ValueError, AttributeError) as exc:
                errors.append(f"{path}: {type(exc).__name__}")
        if requests:
            completed = 0
            for path in requests:
                result_path = path.parent / "result.json"
                if result_path.exists():
                    try:
                        completed += _read(result_path).get("status") == "completed"
                    except (OSError, ValueError, AttributeError) as exc:
                        errors.append(f"{result_path}: {type(exc).__name__}")
            counts[role].update(adapter_cli_requests=len(requests),
                                adapter_cli_processes=sum((p.parent / "process.json").exists() for p in requests),
                                adapter_cli_completed=completed)
            if counts[role]["request_count"] is None:
                counts[role].update(request_count=len(requests), source="adapter_cli_request_files")
            else:
                counts[role]["source"] += ";adapter_stream_not_added_to_avoid_double_counting"
    for role, name in (("buyer", buyer), ("seller", seller)):
        if name == "kit":
            counts[role].update(request_count=0, source="fixed_counterpart_no_model")
    return {"roles": counts, "trace_errors": errors,
            "shared_http_attribution": "unknown; see global shared_http_calls"}


def _shared_http(directories):
    if not directories:
        return {"scope": "global_only", "request_count": None, "status": "not_supplied",
                "claim": "No per-match attribution or zero-call inference."}
    seen, sources, errors = {}, [], []
    for raw in directories:
        root = Path(raw).resolve()
        sources.append(str(root))
        if not root.is_dir():
            errors.append(f"missing directory: {root}")
            continue
        for path in root.rglob("request.json"):
            try:
                metadata = _read(path)
                if metadata.get("source") != "openai-compatible-http" or _synthetic(metadata):
                    continue
                key = metadata.get("id") or str(path.resolve())
                if key not in seen:
                    result_path = path.parent / "result.json"
                    result = _read(result_path) if result_path.exists() else {}
                    seen[key] = {"request": str(path), "mode": metadata.get("mode"),
                                 "model": metadata.get("model"), "status": result.get("status", "pending"),
                                 "process_started": (path.parent / "process.json").exists()}
            except (OSError, ValueError, AttributeError) as exc:
                errors.append(f"{path}: {type(exc).__name__}")
    return {"scope": "global_only", "request_count": len(seen) if not errors else None,
            "observed_request_count": len(seen), "processes_started": sum(v["process_started"] for v in seen.values()),
            "statuses": dict(Counter(v["status"] for v in seen.values())), "directories": sources,
            "requests": list(seen.values()), "errors": errors,
            "claim": "Global directory observations; not assigned to a run, match, participant, or role."}


def _role(row, participant):
    if row.get("buyer") == participant and row.get("seller") != participant:
        return "buyer"
    if row.get("seller") == participant and row.get("buyer") != participant:
        return "seller"
    return "unknown"


def _calls(rows, role):
    requests = [row["model_evidence"]["roles"][role]["request_count"] for row in rows]
    known = [value for value in requests if value is not None]
    return {"request_count": sum(known) if len(known) == len(rows) else None,
            "observed_request_count": sum(known) if known else None,
            "unknown_attempts": len(rows) - len(known)}


def _group(run, rows, identity):
    mode, kind, participant, role, buyer, seller, item, budget, scope, runner = identity
    prices = [{"match_id": row.get("id"), "repeat": row.get("repeat"), "price": row["price"]}
              for row in rows if row.get("completed_valid") is True and _finite(row.get("price"), positive=True)]
    valid = sum(row.get("completed_valid") is True for row in rows)
    unknown = sum(type(row.get("completed_valid")) is not bool for row in rows)
    our_role = _role(rows[0], "ours")
    participant_meta = run["participants"].get(participant, {})
    issues = [{"match_id": row.get("id"), "issues": row["safety_violations"]}
              for row in rows if row.get("safety_violations")]
    errors = [{"match_id": row.get("id"), "error": row.get("error"), "timeout": row.get("timeout"),
               "buyer_exit_code": row.get("buyer_exit_code")} for row in rows
              if row.get("error") or row.get("timeout") or row.get("buyer_exit_code") not in (None, 0)]
    participant_calls = _calls(rows, role) if role in {"buyer", "seller"} else {
        "request_count": None, "observed_request_count": None, "unknown_attempts": len(rows)}
    return {"run": run["run"], "mode": mode, "kind": kind, "participant": participant,
            "actual_role": role, "buyer": buyer, "seller": seller, "our_role": our_role,
            "pairing": ("our_buyer_vs_peer_seller" if buyer == "ours" and kind == "cross" else
                        "peer_buyer_vs_our_seller" if seller == "ours" and kind == "cross" else "fixed_counterpart" if kind == "fixed" else "unknown"),
            "item": item, "budget": budget, "execution_scope": json.loads(scope), "runner_sha256": runner,
            "participant_commit": participant_meta.get("source_commit", participant_meta.get("head_sha")),
            "participant_source_sha256": run["source_hashes"].get(participant),
            "attempts": len(rows), "completed_valid": valid if not unknown else None,
            "observed_completed_valid": valid, "unknown_outcomes": unknown,
            "prices": {"mean": _mean([v["price"] for v in prices]),
                       "min": min((v["price"] for v in prices), default=None),
                       "max": max((v["price"] for v in prices), default=None),
                       "all_individual": prices, "missing_completed_prices": valid - len(prices)},
            "surplus_per_attempt": _surplus(rows, role)["per_attempt"] if role in {"buyer", "seller"} else None,
            "surplus_details": {role: _surplus(rows, role) for role in ("buyer", "seller")},
            "our_surplus_per_attempt": _surplus(rows, our_role)["per_attempt"] if our_role in {"buyer", "seller"} else None,
            "elapsed_seconds": _observed_mean(rows, "elapsed_seconds"),
            "rpc_calls": _observed_mean(rows, "rpc_calls"),
            "safety_issues": issues, "safety_affected_attempts": len(issues), "errors": errors,
            "model_calls": {**participant_calls,
                            "roles": {r: _calls(rows, r) for r in ("buyer", "seller")},
                            "claim": "Participant-role request attempts; SDK/provider internal retries are not inferred."},
            "individual_results": rows}


def load_run(raw):
    supplied = Path(raw).resolve()
    summary = supplied / "summary.json" if supplied.is_dir() else supplied
    root = summary.parent
    manifest_path = root / "manifest.json"
    manifest = _read(manifest_path) if manifest_path.exists() else {}
    arguments = manifest.get("arguments", {})
    run = {"run": str(root), "input": str(supplied), "summary_path": str(summary),
           "status": "checkpointing", "mode": arguments.get("mode"), "kind": arguments.get("kind"),
           "participants": manifest.get("participants", {}), "source_hashes": manifest.get("source_hashes", {}),
           "manifest_runner_sha256": manifest.get("runner_sha256"), "kit_sha256": manifest.get("kit_sha256"),
           "claim_scope": manifest.get("claim_scope"), "controlled_model": manifest.get("controlled_model"),
           "source_unchanged": None, "groups": [], "excluded_synthetic": []}
    for key in ("claim_limits", "limitations"):
        if key in manifest:
            run["manifest_" + key] = manifest[key]
    if not summary.exists():
        checkpoints = sorted(root.glob("checkpoint-*.json"))
        run["latest_checkpoint"] = str(checkpoints[-1]) if checkpoints else None
        run["observed_checkpoint_records"] = len(_read(checkpoints[-1])) if checkpoints else None
        return run
    payload = _read(summary)
    run["input_sha256"] = hashlib.sha256(summary.read_bytes()).hexdigest()
    if isinstance(payload, list) or summary.name.startswith("checkpoint-"):
        run["observed_checkpoint_records"] = len(payload) if isinstance(payload, list) else len(payload.get("results", []))
        return run
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        run.update(status="invalid_input", error="Expected a benchmark summary with a results list")
        return run
    rows = payload["results"]
    run.update(mode=payload.get("mode", run["mode"]), kind=payload.get("kind", run["kind"]),
               summary_runner_sha256=payload.get("runner_sha256"),
               source_unchanged=payload.get("source_unchanged"), declared_attempts=payload.get("attempts"),
               declared_completed_valid=payload.get("completed_valid"), observed_records=len(rows))
    for key in ("claim_scope", "claim_limits", "limitations"):
        if key in payload:
            run["summary_" + key] = payload[key]
    if payload.get("status") in {"running", "checkpointing", "in_progress"}:
        return run
    if payload.get("attempts") is not None and payload["attempts"] != len(rows):
        run["checkpoint_note"] = "Declared attempts differ from available rows; no outcome aggregates produced."
        return run
    if _synthetic(payload):
        run.update(status="synthetic_only", excluded_synthetic=[{"reason": "synthetic summary", "records": len(rows)}])
        return run
    buckets = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Benchmark result must be an object")
        if _synthetic(row):
            run["excluded_synthetic"].append({"match_id": row.get("id"), "reason": "explicit synthetic/stub marker"})
            continue
        row = dict(row)
        participant = row.get("participant")
        if not participant:
            if row.get("buyer") == "kit":
                participant = row.get("seller")
            elif row.get("seller") == "kit":
                participant = row.get("buyer")
            else:
                participant = row.get("seller") if row.get("buyer") == "ours" else row.get("buyer")
        role = _role(row, participant)
        row["actual_participant_role"] = role
        row["role_label_disagrees"] = row.get("participant_role") not in (None, role)
        match_id = row.get("id")
        match_dir = root / str(match_id)
        if not isinstance(match_id, str) or not match_dir.resolve().is_relative_to(root):
            row["model_evidence"] = {"roles": {r: {"request_count": None} for r in ("buyer", "seller")},
                                     "trace_errors": ["Missing or unsafe match id"]}
        else:
            row["model_evidence"] = _trace_counts(match_dir, row.get("buyer"), row.get("seller"))
        runner = row.get("runner_sha256", payload.get("runner_sha256", manifest.get("runner_sha256")))
        identity = (row.get("mode", run["mode"]), run["kind"], participant, role,
                    row.get("buyer"), row.get("seller"), row.get("item"), row.get("budget"),
                    json.dumps(row.get("execution_scope"), sort_keys=True), runner)
        buckets.setdefault(identity, []).append(row)
    run["status"] = "complete" if buckets or not rows else "synthetic_only"
    run["groups"] = [_group(run, values, identity) for identity, values in buckets.items()]
    return run


def summarize(paths, *, shared_http_traces=()):
    runs, seen = [], set()
    for raw in paths:
        path = Path(raw).resolve()
        canonical = path / "summary.json" if path.is_dir() else path
        if canonical in seen:
            continue
        seen.add(canonical)
        try:
            runs.append(load_run(path))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            runs.append({"run": str(canonical.parent), "input": str(path), "status": "invalid_input",
                         "error": f"{type(exc).__name__}: {exc}", "groups": []})
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "claim_limits": CLAIM_LIMITS, "runs": runs,
            "groups": [group for run in runs for group in run["groups"]],
            "shared_http_calls": _shared_http(shared_http_traces)}


def markdown(report):
    def cell(value):
        if value is None:
            return "unknown"
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| Run | Mode/kind | Participant/actual role | Pair | Item | Attempts | Valid | Price mean [min,max] | Surplus/attempt | Seconds | RPC | Safety/errors | Model requests |",
             "|---|---|---|---|---|---:|---:|---|---:|---:|---:|---|---:|"]
    for group in report["groups"]:
        prices = group["prices"]
        values = [Path(group["run"]).name, f"{group['mode']}/{group['kind']}",
                  f"{group['participant']}/{group['actual_role']}", f"{group['buyer']} -> {group['seller']}",
                  group["item"], group["attempts"], group["completed_valid"],
                  f"{cell(prices['mean'])} [{cell(prices['min'])},{cell(prices['max'])}]",
                  group["surplus_per_attempt"], group["elapsed_seconds"]["mean"], group["rpc_calls"]["mean"],
                  f"{group['safety_affected_attempts']}/{len(group['errors'])}", group["model_calls"]["request_count"]]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    for run in report["runs"]:
        if run["status"] != "complete":
            lines.append(f"\n{cell(run['run'])}: **{run['status']}** (outcome totals withheld).")
    lines.append("\nShared HTTP model requests (global only): " + cell(report["shared_http_calls"]["request_count"]))
    lines.append("\nObserved evidence only; no ranking or win-rate inference. Unknown values are not zero.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--shared-http-trace", action="append", default=[])
    args = parser.parse_args(argv)
    out = Path(args.out)
    report = summarize(args.summaries, shared_http_traces=args.shared_http_trace)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.write("\n")
    print(markdown(report))
    return report


if __name__ == "__main__":
    main()
