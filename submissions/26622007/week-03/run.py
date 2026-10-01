"""CLI: plan, smoke, run, summary. Only real API runs enter results.csv."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
import uuid

from contract_net import CONDITIONS, bid_response_format, load_tasks, make_team, messages_for, run_round
from openrouter_client import ConfigurationError, OpenRouterClient, read_key, redact

ROOT = Path(__file__).resolve().parent
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_config(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigurationError("config must be an object")
    if data.get("model") != "deepseek/deepseek-v4.1-flash":
        raise ConfigurationError("this experiment fixes model to deepseek/deepseek-v4.1-flash")
    for name in ("max_attempts", "max_http_requests") + (("max_tokens",) if "max_tokens" in data else ()):
        if type(data.get(name)) is not int or data[name] < 1:
            raise ConfigurationError(f"{name} must be a positive integer")
    for name in ("temperature", "timeout_seconds", "retry_delay_seconds"):
        value = data.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ConfigurationError(f"{name} must be a finite nonnegative number")
    if data["timeout_seconds"] == 0 or data["temperature"] > 2 or data["max_attempts"] > 3:
        raise ConfigurationError("timeout must be positive, temperature <= 2, max_attempts <= 3")
    deadline = data.get("response_deadline_seconds")
    if type(deadline) not in (int, float) or not 0 < deadline <= 180:
        raise ConfigurationError("response_deadline_seconds must be positive and <= 180")
    if data.get("reasoning") != {"enabled": False}:
        raise ConfigurationError("this experiment fixes reasoning.enabled=false")
    provider = data.get("provider", {})
    if (not isinstance(provider, dict) or provider.get("allow_fallbacks") is not True
            or provider.get("require_parameters") is not True):
        raise ConfigurationError("enable provider fallbacks and require parameter support")
    prices = provider.get("max_price", {})
    for field in ("prompt", "completion"):
        value = prices.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ConfigurationError("provider max_price needs positive prompt and completion rates")
    return data


def snapshot(config):
    source_hashes = {p.name: digest(p.read_bytes()) for p in sorted(ROOT.glob("*.py"))
                     if not p.name.startswith("test_")}
    state = {"config": config, "tasks_sha256": digest((ROOT / "tasks.json").read_bytes()),
             "source_sha256": source_hashes,
             "prompts": {condition: {c.name: c.system for c in make_team(condition)}
                         for condition in CONDITIONS},
             "memory_policy": "fresh two-message context per task and contractor; no result feedback",
             "order": ["A", "B", "C"], "tie_break": "first in A,B,C order",
             "python": platform.python_version()}
    state["experiment_id"] = digest(json.dumps(state, sort_keys=True, ensure_ascii=False).encode())[:16]
    return state


def require_committed_tasks():
    try:
        repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL, text=True).strip())
        task_path = (ROOT / "tasks.json").relative_to(repo).as_posix()
        committed = subprocess.check_output(["git", "show", f"HEAD:{task_path}"], cwd=ROOT,
                                            stderr=subprocess.DEVNULL)
        if committed != (ROOT / "tasks.json").read_bytes():
            raise ConfigurationError("Commit tasks.json before live execution.")
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, ValueError):
        raise ConfigurationError("A Git commit containing tasks.json is required before live execution.") from None


class Recorder:
    def __init__(self, stream, run_id, key):
        self.stream, self.run_id, self.key = stream, run_id, key
        self.sequence = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                      "reported_cost_usd": 0.0, "cost_missing_responses": 0}

    def emit(self, event, **fields):
        self.sequence += 1
        if event == "usage":
            usage = fields.get("usage") or {}
            for name in ("prompt_tokens", "completion_tokens"):
                self.usage[name] += usage.get(name) or 0
            details = usage.get("completion_tokens_details") or {}
            self.usage["reasoning_tokens"] += details.get("reasoning_tokens") or 0
            cost = usage.get("cost")
            if type(cost) in (int, float):
                self.usage["reported_cost_usd"] += cost
            else:
                self.usage["cost_missing_responses"] += 1
        record = {"seq": self.sequence, "at": datetime.now(timezone.utc).isoformat(),
                  "run": self.run_id, "event": event, **fields}
        line = redact(json.dumps(record, ensure_ascii=False, allow_nan=False), self.key)
        print(line, file=self.stream, flush=True)
        print(line, flush=True)


def append_result(path, row):
    new = not path.exists()
    if not new:
        with path.open(newline="", encoding="utf-8") as stream:
            if next(csv.reader(stream), None) != HEADER:
                raise ConfigurationError("Existing results.csv has a different header; preserve it and inspect.")
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADER)
        if new:
            writer.writeheader()
        writer.writerow(row)


def run_one(tasks, golds, condition, client, state, smoke=False):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = ROOT / ("diagnostics" if smoke else "logs")
    folder.mkdir(exist_ok=True)
    path = folder / f"{'smoke-' if smoke else ''}{run_id}-{condition}.log"
    row = dict.fromkeys(HEADER, "")
    row.update(run=run_id, condition=condition)
    before = client.request_count
    with path.open("x", encoding="utf-8") as stream:
        recorder = Recorder(stream, run_id, client.key)
        emit = recorder.emit
        emit("start", mode="smoke" if smoke else "experiment", condition=condition, settings=state)
        try:
            def call(task, contractor):
                return client.complete(messages_for(task, contractor), emit, task.id, contractor.name,
                                       response_format=bid_response_format())
            result = run_round(tasks, make_team(condition), call, emit)
            scores = result.evaluate(golds)
            for task in tasks:
                emit("evaluation", task_id=task.id, gold=golds[task.id], winner=result.assignments[task.id])
            row.update(scores)
            note = {"status": "completed", "parse_fails": result.parse_fails, "refusals": result.refusals}
            emit("complete", **scores, **note)
        except (Exception, KeyboardInterrupt) as exc:
            note = {"status": "crashed", "error": redact(f"{type(exc).__name__}: {exc}", client.key)}
            emit("crash", **note)
        note.update(experiment_id=state["experiment_id"],
                    http_requests=client.request_count - before, **recorder.usage)
        row["note"] = json.dumps(note, ensure_ascii=False, separators=(",", ":"))
        emit("run_record", row=row, output_csv=None if smoke else "results.csv")
    if not smoke:
        append_result(ROOT / "results.csv", row)
    return note["status"] == "completed"


def show_summary():
    path = ROOT / "results.csv"
    if not path.exists():
        print("아직 실제 실험 결과가 없습니다. 모의 테스트는 results.csv에 기록하지 않습니다.")
        return
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != HEADER:
            raise ConfigurationError("Invalid results.csv header")
        rows = list(reader)
    print("| run | condition | correct/tasks | messages | unassigned | misawards | status | experiment |")
    print("|---|---|---:|---:|---:|---:|---|---|")
    for row in rows:
        note = json.loads(row["note"])
        print(f"| {row['run']} | {row['condition']} | {row['correct']}/{row['tasks']} | "
              f"{row['messages']} | {row['unassigned']} | {row['misawards']} | "
              f"{note['status']} | {note['experiment_id']} |")
    if len({json.loads(r["note"])["experiment_id"] for r in rows}) > 1:
        print("주의: 설정·코드·작업이 다른 실험이 포함됩니다. experiment별로 분리해서 비교하세요.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "smoke", "run", "summary"), nargs="?", default="plan")
    parser.add_argument("--condition", choices=("all",) + CONDITIONS, default="all")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.command == "summary":
        show_summary()
        return 0
    if args.repeats < 1:
        raise ConfigurationError("repeats must be positive")
    config = load_config(ROOT / "config.json")
    tasks, golds = load_tasks((ROOT / "tasks.json").read_text(encoding="utf-8"))
    conditions = CONDITIONS if args.condition == "all" else (args.condition,)
    state = snapshot(config)
    expected = len(tasks) * 3 * len(conditions) * args.repeats
    env_file = args.env_file
    if env_file is None:
        # The course checker scans every file under week-03, including ignored files.
        # Keep credentials one directory above the checked submission.
        candidate = ROOT.parent / ".env"
        env_file = candidate if candidate.exists() else None
    if args.command == "plan":
        try:
            read_key(env_file)
            credential = "OpenRouter key format found; API validity not checked"
        except ConfigurationError as exc:
            credential = str(exc)
        print(json.dumps({"model": config["model"], "task_count": len(tasks),
                          "conditions": conditions, "runs": len(conditions) * args.repeats,
                          "expected_calls": expected, "max_calls_with_retries": expected * config["max_attempts"],
                          "request_budget": config["max_http_requests"], "config": config,
                          "experiment_id": state["experiment_id"], "credential": credential},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "run" and expected * config["max_attempts"] > config["max_http_requests"]:
        raise ConfigurationError("Planned requests exceed max_http_requests; no call sent.")
    state["git_commit"] = require_committed_tasks()
    key = read_key(env_file)
    client = OpenRouterClient(key, config)
    with (ROOT / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigurationError("Another run owns this output directory.") from None
        results_path = ROOT / "results.csv"
        if results_path.exists():
            with results_path.open(newline="", encoding="utf-8") as stream:
                if next(csv.reader(stream), None) != HEADER:
                    raise ConfigurationError("Existing results.csv header is invalid; no call sent.")
        if args.command == "smoke":
            return 0 if run_one(tasks[:1], golds, conditions[0], client, state, smoke=True) else 1
        for condition in conditions:
            for _ in range(args.repeats):
                if not run_one(tasks, golds, condition, client, state):
                    print("실패한 실행을 보존하고 중단했습니다. 원인을 확인한 뒤 새 실행으로 재개하세요.")
                    return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigurationError, ValueError) as exc:
        print(f"설정 확인: {exc}", file=sys.stderr)
        raise SystemExit(2)
