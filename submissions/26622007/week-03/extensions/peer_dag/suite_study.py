"""Frozen five-case, three-condition, three-repeat study; every attempt is retained."""
import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from jsonschema import Draft202012Validator
from audit import audit
from case_catalog import catalog, load_case
from cli import ROOT, load_config
from condition_study import canonical_facts, save
from core import fingerprint
from models import BASE, CONDITIONS, messages, team_roster
from response_formats import response_format

PROTOCOL = ROOT / "conditions/SUITE_NO_TOKEN_LIMIT_PROTOCOL.md"
DEADLINE_SECONDS = 600
GAP_SECONDS = 15


def schedule(batch_id):
    ids = [entry["id"] for entry in catalog()]
    result = []
    for block in range(1, 4):
        ordered_cases = ids[block - 1:] + ids[:block - 1]
        conditions = CONDITIONS[block - 1:] + CONDITIONS[:block - 1]
        for case_id in ordered_cases:
            for condition in conditions:
                result.append({"case_id": case_id, "condition": condition, "block": block,
                               "run_id": f"{batch_id}-r{block}-{case_id}-{condition}"})
    return result


def frozen_sources():
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    files = {*ROOT.glob("*.py"), ROOT / "config.json", ROOT / "cases/README.md", PROTOCOL,
             BASE / "contract_net.py", BASE / "openrouter_client.py", BASE / "tasks.json"}
    for entry in catalog():
        files.update(load_case(entry["id"])[2])
    hashes = {}
    for path in sorted(files):
        relative = path.relative_to(repo).as_posix()
        committed = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
        if committed != path.read_bytes():
            raise ValueError(f"commit {relative} before live study")
        hashes[relative] = fingerprint(path.read_text())
    return hashes


def command(item):
    return [sys.executable, "-u", str(ROOT / "cli.py"), "live", "--case", item["case_id"],
            "--condition", item["condition"], "--requester", "A", "--run-id", item["run_id"]]


async def attempt_run(folder, item):
    started, timed_out = time.monotonic(), False
    print(json.dumps({"event": "attempt_start", **item}), flush=True)
    with (folder / f"{item['run_id']}.console.log").open("x", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command(item)) + "\n")
        stream.flush()
        process = await asyncio.create_subprocess_exec(*command(item), stdout=stream, stderr=asyncio.subprocess.STDOUT)
        try:
            await asyncio.wait_for(process.wait(), DEADLINE_SECONDS)
        except asyncio.TimeoutError:
            timed_out = True
            process.terminate()
            await process.wait()
        except BaseException:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            raise
        stream.write(f"\nexit_code={process.returncode}\ntimed_out={timed_out}\n")
    attempt = {**item, "exit_code": process.returncode, "timed_out": timed_out,
               "wall_seconds": round(time.monotonic() - started, 4)}
    save(folder / f"{item['run_id']}.attempt.json", attempt)
    return attempt


def inspect_run(attempt):
    case_id, run_id, condition = (attempt[key] for key in ("case_id", "run_id", "condition"))
    expected = load_case(case_id)[1]
    log = ROOT / "logs" / f"{run_id}.jsonl"
    rows, errors = [], []
    if log.exists():
        for number, line in enumerate(log.read_text().splitlines(), 1):
            try:
                rows.append(json.loads(line))
            except ValueError:
                errors.append({"kind": "trace_decode", "line": number})
    state = next((row["settings"] for row in rows if row["event"] == "run_start"), {})
    trace = (audit(log) if log.exists() and not errors else
             {"passed": False, "errors": ["missing or incomplete trace"]})
    def optional_json(path):
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            errors.append({"kind": "artifact_decode", "path": str(path)})
            return {}
    result = optional_json(ROOT / "runs" / run_id / "result.json")
    artifact = optional_json(ROOT / "runs" / run_id / "artifacts" / f"{case_id}.json").get("artifact")
    requests, active, intervals, responses = {}, {}, [], 0
    request_errors, response_errors = [], []
    for row in rows:
        event = row["event"]
        if event == "http_request":
            key = (row["task_id"], row["contractor"], row["phase"], row["attempt"])
            if key in requests:
                request_errors.append({"kind": "duplicate_request", "key": key})
            requests[key] = row["payload"]
            try:
                payload = json.loads(row["payload"]["messages"][1]["content"])
                checks = {
                    "messages": row["payload"]["messages"] == messages(row["contractor"], row["phase"], payload, condition),
                    "format": row["payload"].get("response_format") == response_format(row["phase"], payload),
                    "transport": all(row["payload"].get(field) == state.get("transport", {}).get(field)
                                     for field in ("model", "temperature", "max_tokens", "reasoning", "provider")),
                    "no_private_memory_or_expected": not ({"expected", "gold", "personal_memory", "handoff_memory"} & payload.keys()),
                }
                for name, passed in checks.items():
                    if not passed:
                        request_errors.append({"kind": name, "key": key})
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                request_errors.append({"kind": "request_decode", "key": key, "message": str(exc)})
        elif event == "http_response":
            responses += 1
            key = (row["task_id"], row["contractor"], row["phase"], row["attempt"])
            try:
                envelope = json.loads(row["raw_response"])
                content = json.loads(envelope["choices"][0]["message"]["content"])
                schema = requests[key]["response_format"]["json_schema"]["schema"]
                Draft202012Validator.check_schema(schema)
                for failure in Draft202012Validator(schema).iter_errors(content):
                    response_errors.append({"kind": "response_schema", "key": key, "message": failure.message})
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                response_errors.append({"kind": "response_decode", "key": key, "message": str(exc)})
        elif event == "call_start" and row["phase"] in ("execute", "synthesize"):
            active[(row["task_id"], row["worker"], row["phase"])] = row["at"]
        elif event == "call_end" and row["phase"] in ("execute", "synthesize"):
            key = (row["task_id"], row["worker"], row["phase"])
            start = active.pop(key, None)
            if start:
                intervals.append({"task_id": key[0], "worker": key[1], "start": start, "end": row["at"]})
    overlaps = []
    for left, right in combinations(intervals, 2):
        start = max(datetime.fromisoformat(left["start"]), datetime.fromisoformat(right["start"]))
        end = min(datetime.fromisoformat(left["end"]), datetime.fromisoformat(right["end"]))
        if end > start:
            overlaps.append({"left": left["task_id"], "right": right["task_id"],
                             "workers": [left["worker"], right["worker"]], "seconds": (end - start).total_seconds(),
                             "both_tasks_succeeded": all(trace.get("task_statuses", {}).get(p["task_id"]) == "succeeded"
                                                         for p in (left, right))})
    if (state.get("case_id"), state.get("condition"), state.get("team_roster")) != (case_id, condition, team_roster(condition)):
        errors.append({"kind": "settings_mismatch"})
    phases = Counter(row["phase"] for row in rows if row["event"] == "call_start")
    awards = [row for row in rows if row["event"] == "award"]
    proposals = [row for row in rows if row["event"] == "proposal"]
    c_proposals = [row["proposal"] for row in proposals if row["worker"] == "C"]
    root_award = next((row for row in awards if row["task_id"] == case_id), {})
    controls = {key: state.get(key) for key in ("requester", "limits", "transport", "case_sha", "expected_sha", "sources",
                "base_sources", "selection_policy", "concurrency_policy", "response_format_policy")}
    facts = artifact.get("facts") if artifact else None
    metrics = {**attempt, "status": result.get("status", "timeout" if attempt["timed_out"] else "crashed"),
               "passed": bool(result.get("evaluation", {}).get("passed")),
               "checks_passed": sum(result.get("evaluation", {}).get("checks", {}).values()), "checks_total": len(expected),
               "wrong_or_missing": [key for key in expected if not result.get("evaluation", {}).get("checks", {}).get(key)],
               "calls": sum(phases.values()), "http_requests": len(requests), "http_responses": responses,
               "http_429": sum(row["event"] == "http_http_error" and row["status"] == 429 for row in rows),
               "http_errors": dict(Counter(str(row["status"]) for row in rows if row["event"] == "http_http_error")),
               "retries": sum(row["event"] == "http_retry" for row in rows),
               "transport_errors": sum(row["event"] == "http_transport_error" for row in rows),
               "tasks": sum(row["event"] == "task_start" for row in rows),
               "max_depth": max((row["depth"] for row in rows if row["event"] == "task_start"), default=0),
               "awards": dict(Counter(row["worker"] for row in awards)), "root_award": root_award.get("worker"),
               "c_proposals": len(c_proposals), "c_high_bids": sum(p["bid"] and p["confidence"] >= 95 for p in c_proposals),
               "proposal_rejections": [{"task_id": row["task_id"], "worker": row["worker"], "error": row["error"]}
                                       for row in rows if row["event"] == "proposal_rejected"],
               "task_failures": [{"task_id": row["task_id"], "error": row["outcome"]["error"]}
                                 for row in rows if row["event"] == "task_end" and row["outcome"]["status"] == "failed"],
               "reported_cost_usd": result.get("reported_cost_usd"), "cost_missing_responses": result.get("cost_missing_responses"),
               "elapsed_seconds": result.get("elapsed_seconds", attempt["wall_seconds"]),
               "canonical_facts": canonical_facts(facts, expected), "artifact_sha": fingerprint(artifact) if artifact else None,
               "root_plan_sha": fingerprint(root_award["plan"]) if root_award else None,
               "award_sha": fingerprint({row["task_id"]: row["worker"] for row in awards}),
               "controls_sha": fingerprint(controls), "root_plan": root_award.get("plan"), "intervals": intervals,
               "overlaps": overlaps, "trace": trace, "request_checks_passed": bool(requests) and not request_errors,
               "response_checks_passed": responses > 0 and not response_errors,
               "verification_errors": errors + request_errors + response_errors,
               "error": result.get("error", "run did not produce result.json"),
               "recorded_case_condition_match": state.get("case_id") == case_id and state.get("condition") == condition}
    return metrics


def group_metrics(runs):
    awards = Counter()
    for run in runs:
        awards.update(run["awards"])
    return {"attempts": len(runs), "passed": sum(r["passed"] for r in runs),
            "checks_passed": sum(r["checks_passed"] for r in runs), "checks_total": sum(r["checks_total"] for r in runs),
            "status_succeeded": sum(r["status"] == "succeeded" for r in runs),
            "depths": dict(Counter(r["max_depth"] for r in runs)),
            "parallel_runs": sum(bool(r["overlaps"]) for r in runs),
            "same_worker_parallel_runs": sum(any(o["workers"][0] == o["workers"][1] for o in r["overlaps"]) for r in runs),
            "calls": sum(r["calls"] for r in runs), "http_requests": sum(r["http_requests"] for r in runs),
            "http_429": sum(r["http_429"] for r in runs), "retries": sum(r["retries"] for r in runs),
            "elapsed_seconds": sum(r["elapsed_seconds"] for r in runs),
            "reported_cost_usd": sum(r["reported_cost_usd"] or 0 for r in runs), "awards": dict(awards),
            "c_root_awards": sum(r["root_award"] == "C" for r in runs),
            "c_high_bids": sum(r["c_high_bids"] for r in runs), "c_proposals": sum(r["c_proposals"] for r in runs)}


def summarize(folder, manifest, metrics):
    cases = {}
    for entry in catalog():
        groups = {}
        for condition in CONDITIONS:
            subset = [m for m in metrics if m["case_id"] == entry["id"] and m["condition"] == condition]
            groups[condition] = {**group_metrics(subset),
                "runs_with_facts": sum(m["canonical_facts"] is not None for m in subset),
                "distinct_fact_vectors": len({fingerprint(m["canonical_facts"]) for m in subset if m["canonical_facts"] is not None}),
                "distinct_full_artifacts": len({m["artifact_sha"] for m in subset if m["artifact_sha"]}),
                "distinct_root_plans": len({m["root_plan_sha"] for m in subset if m["root_plan_sha"]})}
        cases[entry["id"]] = groups
    integrity = {"exact_schedule": [{key: m[key] for key in ("case_id", "condition", "block", "run_id")} for m in metrics] == manifest["schedule"],
                 "source_unchanged": frozen_sources() == manifest["source_hashes"],
                 "case_condition_match": all(m["recorded_case_condition_match"] for m in metrics),
                 "same_controls_per_case": all(len({m["controls_sha"] for m in metrics if m["case_id"] == e["id"]}) == 1 for e in catalog()),
                 "request_checks": all(m["request_checks_passed"] for m in metrics),
                 "response_checks": all(m["response_checks_passed"] for m in metrics),
                 "trace_checks": all(m["trace"]["passed"] for m in metrics)}
    summary = {"manifest": manifest, "integrity": integrity, "total": group_metrics(metrics), "cases": cases,
               "conditions": {c: group_metrics([m for m in metrics if m["condition"] == c]) for c in CONDITIONS}, "runs": metrics}
    save(folder / "summary.json", summary)
    columns = ("run_id", "case_id", "condition", "block", "status", "passed", "checks_passed", "checks_total", "max_depth",
               "calls", "http_requests", "http_responses", "http_429", "retries", "root_award", "elapsed_seconds", "reported_cost_usd")
    with (folder / "results.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metrics)
    return summary


async def run_study(folder, manifest):
    metrics = []
    for index, item in enumerate(manifest["schedule"]):
        if frozen_sources() != manifest["source_hashes"]:
            raise ValueError("frozen source changed; existing attempts retained")
        attempt = await attempt_run(folder, item)
        metric = inspect_run(attempt)
        save(folder / f"{item['run_id']}.metrics.json", metric)
        metrics.append(metric)
        print(json.dumps({"event": "attempt_end", "index": index + 1, **{key: metric[key] for key in
                         ("run_id", "status", "passed", "checks_passed", "checks_total", "calls", "max_depth", "http_429", "elapsed_seconds")}}, ensure_ascii=False), flush=True)
        if index + 1 < len(manifest["schedule"]):
            await asyncio.sleep(GAP_SECONDS)
    summary = summarize(folder, manifest, metrics)
    print(json.dumps({"event": "study_end", "folder": str(folder), "integrity": summary["integrity"], "total": summary["total"]}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "run"))
    args = parser.parse_args()
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-no-token-limit-" + uuid.uuid4().hex[:6]
    plan = {"batch_id": batch_id, "schedule": schedule(batch_id), "config": load_config()[0],
            "protocol": str(PROTOCOL.relative_to(ROOT)), "deadline_seconds": DEADLINE_SECONDS,
            "inter_run_delay_seconds": GAP_SECONDS, "parallel_experiments": 1,
            "planned_runs": 45, "planned_fact_checks": 9 * sum(len(load_case(e["id"])[1]) for e in catalog())}
    if {"max_tokens", "max_completion_tokens"} & plan["config"]["transport"].keys():
        raise ValueError("this correction study must omit output token limits")
    plan["output_token_policy"] = "omit max_tokens and max_completion_tokens; provider defaults apply"
    if args.mode == "plan":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    folder = ROOT / "conditions" / batch_id
    plan.update(source_hashes=frozen_sources(), git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
    folder.mkdir()
    save(folder / "manifest.json", plan)
    print(json.dumps({"event": "study_start", "folder": str(folder), "runs": len(plan["schedule"])}), flush=True)
    asyncio.run(run_study(folder, plan))


if __name__ == "__main__":
    main()
