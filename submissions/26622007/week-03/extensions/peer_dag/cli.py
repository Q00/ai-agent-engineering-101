"""Run a scripted demo, one bounded live experiment, or exact-input replay."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import time
import uuid

from core import Limits, Outcome, Runtime, Task, evaluate, fingerprint
from case_catalog import catalog, load_case, select_case
from fixtures import DemoModel
from models import BASE, CONDITIONS, LiveModel, ReplayModel, redact, team_roster
from openrouter_client import ConfigurationError, rate_limit_policy, read_key

ROOT = Path(__file__).resolve().parent


def load_config():
    config = json.loads((ROOT / "config.json").read_text())
    limits = Limits(**{k: v for k, v in config.items() if k != "transport"})
    transport = config["transport"]
    if transport.get("provider", {}).get("require_parameters") is not True:
        raise ValueError("structured output requires provider.require_parameters=true")
    deadline = transport.get("response_deadline_seconds")
    if type(deadline) not in (int, float) or not 0 < deadline <= 180:
        raise ValueError("response_deadline_seconds must be positive and <= 180")
    for key in ("max_attempts", "max_http_requests") + (("max_tokens",) if "max_tokens" in transport else ()):
        if type(transport.get(key)) is not int or transport[key] < 1:
            raise ValueError(f"invalid transport {key}")
    for key in ("timeout_seconds", "retry_delay_seconds", "temperature"):
        value = transport.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid transport {key}")
    rate_policy = rate_limit_policy(transport)
    if (transport["timeout_seconds"] == 0 or transport["max_attempts"] > 2
            or transport["timeout_seconds"] > 60
            or transport["temperature"] > 2
            or transport["max_http_requests"] != rate_policy["max_attempts"]):
        raise ValueError("transport needs a positive timeout, <=2 ordinary attempts and a matching 429 request budget")
    return config, limits


class Recorder:
    def __init__(self, stream, key=""):
        self.stream, self.key, self.sequence = stream, key, 0

    def emit(self, event, **fields):
        self.sequence += 1
        record = {"seq": self.sequence, "at": datetime.now(timezone.utc).isoformat(),
                  "event": event, **fields}
        self.stream.write(redact(json.dumps(record, ensure_ascii=False, allow_nan=False), self.key) + "\n")
        self.stream.flush()


def committed_inputs(input_paths=None):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    # Freeze the evaluation contract before the first live request; never transmit it to a Worker.
    paths = input_paths if input_paths is not None else (ROOT / "case.json", ROOT / "expected.json")
    for path in (*paths, ROOT / "config.json"):
        saved = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(repo).as_posix()}"], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError(f"commit {path.relative_to(ROOT)} before live execution")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


async def run(args):
    config, limits = load_config()
    if args.parallel is not None:
        limits = Limits(**dict(asdict(limits), max_parallel=args.parallel))
    case_id = select_case(args.mode, args.case, args.replay)
    task, expected, input_paths = load_case(case_id)
    key, sha = "", ""
    if args.mode == "live":
        sha = committed_inputs(input_paths)
        env_file = args.env_file or BASE.parent / ".env"
        key = read_key(env_file if env_file.exists() or args.env_file else None)
    from response_formats import POLICY
    state = {"mode": args.mode, "condition": args.condition, "requester": args.requester, "limits": asdict(limits),
             "case_id": case_id, "evaluation_scope": "required_scalar_facts_only; design quality needs manual review",
             "response_format_policy": POLICY,
             "concurrency_policy": "task-worker-isolated-v1",
             "team_roster": team_roster(args.condition),
             "transport": config["transport"], "case_sha": fingerprint(asdict(task)),
             "expected_sha": fingerprint(expected),
             "selection_policy": "score, confidence, fixed per-child rotation v2",
             "sources": {p.name: fingerprint(p.read_text()) for p in sorted(ROOT.glob("*.py"))},
             "base_sources": {name: fingerprint((BASE / name).read_text())
                              for name in ("contract_net.py", "openrouter_client.py")},
             "git_commit": sha}
    experiment_id = fingerprint(state)[:16]
    run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + args.mode + "-" + uuid.uuid4().hex[:8])
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / f"{run_id}.jsonl"
    started = time.monotonic()
    with log_path.open("x", encoding="utf-8") as stream:
        recorder = Recorder(stream, key)
        model = (LiveModel(key, config["transport"], recorder.emit, args.condition) if args.mode == "live" else
                 ReplayModel(args.replay, args.replay_delay_ms / 1000, config["transport"], args.condition)
                 if args.mode == "replay" else DemoModel())
        runtime = Runtime(model, limits, recorder.emit)
        recorder.emit("run_start", run_id=run_id, experiment_id=experiment_id, settings=state,
                      replay_source=str(args.replay) if args.replay else None,
                      replay_delay_ms=args.replay_delay_ms)
        try:
            outcome = await runtime.run(task, args.requester)
        except asyncio.CancelledError:
            outcome = Outcome("cancelled", error="interrupted; partial outputs retained")
        evaluation = evaluate(outcome, expected)
        replay_complete = model.complete() if isinstance(model, ReplayModel) else None
        result = {"run_id": run_id, "experiment_id": experiment_id, "mode": args.mode, "condition": args.condition,
                  "status": outcome.status, "error": outcome.error,
                  "evaluation": evaluation, "calls": runtime.calls, "tasks": runtime.tasks,
                  "peak_calls": runtime.peak_calls, "elapsed_seconds": round(time.monotonic() - started, 4),
                  "http_requests": getattr(model, "http_requests", 0),
                  "reported_cost_usd": getattr(model, "cost", 0),
                  "cost_missing_responses": getattr(model, "cost_missing", 0),
                  "replay_complete": replay_complete, "log": str(log_path), "output": str(output)}
        for path, item in runtime.outcomes.items():
            target = (output / "artifacts").joinpath(*path.split("/")).with_suffix(".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redact(json.dumps(asdict(item), ensure_ascii=False, indent=2), key) + "\n")
        (output / "result.json").write_text(redact(json.dumps(result, ensure_ascii=False, indent=2), key) + "\n")
        recorder.emit("run_end", result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if evaluation["passed"] and replay_complete is not False else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("demo", "live", "replay", "plan", "list"))
    parser.add_argument("--case", help="Case ID from list; replay defaults to the case recorded in its log")
    parser.add_argument("--requester", choices=("A", "B", "C"), default="A")
    parser.add_argument("--condition", choices=CONDITIONS, default="baseline")
    parser.add_argument("--run-id", help="Optional unique run ID for a recorded experiment batch")
    parser.add_argument("--parallel", type=int, choices=(1, 2, 3))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--replay-delay-ms", type=int, default=0)
    args = parser.parse_args()
    if args.run_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", args.run_id):
        parser.error("run-id must be 1..100 alphanumeric, underscore or hyphen characters")
    if args.mode == "replay" and args.replay is None:
        parser.error("replay mode requires --replay")
    if not 0 <= args.replay_delay_ms <= 1000:
        parser.error("replay delay must be between 0 and 1000 ms")
    if args.mode == "list":
        print(json.dumps([{"id": item["id"], "title": item["title"]} for item in catalog()],
                         ensure_ascii=False, indent=2))
        return 0
    if args.mode == "plan":
        task, expected, _ = load_case(select_case(args.mode, args.case, args.replay))
        config, limits = load_config()
        if args.parallel is not None:
            limits = Limits(**dict(asdict(limits), max_parallel=args.parallel))
        print(json.dumps({"case_id": task.id, "task": asdict(task), "evaluation_fields": list(expected),
                          "evaluation_scope": "required_scalar_facts_only; design quality needs manual review",
                          "limits": asdict(limits), "transport": config["transport"],
                          "max_http_requests": limits.max_calls * rate_limit_policy(config["transport"])["max_attempts"],
                          "live_execution": "provided-data analysis artifacts only"}, ensure_ascii=False, indent=2))
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigurationError, ValueError, OSError) as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(2)
