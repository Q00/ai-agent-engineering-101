"""Independently check execution ordering and overlap from an append-only run trace."""
import argparse
from collections import Counter
import json
from pathlib import Path


def audit(path):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    errors, active, leases, finished = [], {}, {}, {}
    starts, awards, task_ids = {}, {}, set()
    peak, peak_execution = 0, 0
    peak_by_worker, peak_execution_by_worker = Counter(), Counter()
    phases, providers, models = Counter(), Counter(), Counter()
    state = next((r["settings"] for r in records if r["event"] == "run_start"), None)
    if state is None:
        return {"passed": False, "errors": ["missing run_start"]}
    limits = state.get("limits", state.get("config", {}))
    policy = state.get("concurrency_policy", "worker-serial-v1")
    if policy not in ("worker-serial-v1", "task-worker-isolated-v1"):
        errors.append("unknown concurrency policy")
    for expected_seq, record in enumerate(records, 1):
        if record["seq"] != expected_seq:
            errors.append("non-contiguous sequence")
        event, task_id = record["event"], record.get("task_id", "")
        if event == "task_start":
            if task_id in task_ids:
                errors.append(f"duplicate start: {task_id}")
            task_ids.add(task_id)
            parent = task_id.rsplit("/", 1)[0]
            for dep in record["task"]["depends_on"]:
                if finished.get(f"{parent}/{dep}") != "succeeded":
                    errors.append(f"started before successful predecessor: {task_id} -> {dep}")
        elif event == "task_end":
            finished[task_id] = record["outcome"]["status"]
        elif event == "task_blocked":
            finished[task_id] = "blocked"
        elif event == "award":
            awards[task_id] = {"requester": record["requester"], "worker": record["worker"],
                               "mode": record["plan"]["mode"],
                               "dependencies": {s["id"]: s["depends_on"] for s in record["plan"]["steps"]}}
        elif event == "call_start":
            worker = record["worker"]
            key = (task_id, worker, record["phase"])
            if policy == "worker-serial-v1" and any(w == worker for _, w, _ in active):
                errors.append(f"overlapping calls on same worker: {worker}")
            if any(t == task_id and w == worker for t, w, _ in active):
                errors.append(f"overlapping calls on same task/worker session: {task_id}/{worker}")
            active[key] = True
            phases[record["phase"]] += 1
            starts[key] = record["at"]
            peak = max(peak, len(active))
            peak_execution = max(peak_execution, sum(p in ("execute", "synthesize") for _, _, p in active))
            for w in {w for _, w, _ in active}:
                peak_by_worker[w] = max(peak_by_worker[w], sum(a == w for _, a, _ in active))
                peak_execution_by_worker[w] = max(peak_execution_by_worker[w],
                    sum(a == w and p in ("execute", "synthesize") for _, a, p in active))
            if len(active) > limits["max_parallel"]:
                errors.append("parallel call limit exceeded")
        elif event == "call_end":
            worker = record["worker"]
            if active.pop((task_id, worker, record["phase"]), None) is None:
                errors.append(f"unmatched call_end: {task_id}/{worker}")
        elif event == "execution_start":
            reads, writes = set(record["reads"]), set(record["writes"])
            if any(writes & (r | w) or reads & w for r, w in leases.values()):
                errors.append(f"resource conflict: {task_id}")
            leases[task_id] = (reads, writes)
        elif event == "execution_end":
            if leases.pop(task_id, None) is None:
                errors.append(f"unmatched execution_end: {task_id}")
        elif event in ("http_usage", "web_usage"):
            providers[record.get("provider") or "unknown"] += 1
            models[record.get("model") or "unknown"] += 1
    if active or leases:
        errors.append("unfinished calls or leases")
    endings = [r for r in records if r["event"] == "run_end"]
    if len(endings) != 1:
        errors.append("missing or repeated run_end")
    if sum(phases.values()) > limits["max_calls"]:
        errors.append("call budget exceeded")
    if len(finished) > limits["max_tasks"]:
        errors.append("task budget exceeded")
    if any(task_id not in finished for task_id in task_ids):
        errors.append("task without terminal state")
    return {"passed": not errors, "errors": errors, "mode": state.get("mode", "research-live"),
            "concurrency_policy": policy,
            "peak_calls": peak, "peak_execution_calls": peak_execution,
            "peak_calls_by_worker": dict(peak_by_worker),
            "peak_execution_calls_by_worker": dict(peak_execution_by_worker),
            "calls_by_phase": dict(phases), "task_statuses": finished,
            "awards": awards, "providers": dict(providers), "models": dict(models),
            "evaluation": endings[0]["result"]["evaluation"] if endings else None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    result = audit(args.log)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
