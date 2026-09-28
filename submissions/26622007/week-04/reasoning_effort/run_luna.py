"""GPT-6 Luna at low and max reasoning effort; 30-message limit, original prompts and parsers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lab"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# An isolated copy: the English 8-turn module keeps MAX_TURNS=8.
lab = load_module("luna_effort_protocol", ROOT / "lab/experiment.py")
lab.MAX_TURNS = 30
EFFORTS = ("low", "max")
SUITE = "luna-effort-20260928"
SCHEDULE_SEED = 20260928
USAGE = ["http_requests", "prompt_tokens", "completion_tokens", "reasoning_tokens", "cost_usd"]
HEADER = lab.HEADER + ["reasoning_effort", "status", "elapsed_seconds"] + USAGE


def effort_config(base, effort):
    if effort not in EFFORTS:
        raise ValueError(f"Unsupported effort {effort!r}")
    if base.get("reasoning") is not None:
        raise ValueError("Base config must leave reasoning for the effort to fill")
    return {**base, "reasoning": {"effort": effort}}


class UsageMeter:
    """Sum what the API reported for one episode; missing fields stay visible as blanks."""
    def __init__(self):
        self.requests, self.totals, self.missing = 0, dict.fromkeys(USAGE[1:], 0), set()

    def observe(self, event, fields):
        if event == "request":
            self.requests += 1
        elif event == "usage":
            usage = fields.get("usage") or {}
            values = {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens"),
                      "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                      "cost_usd": usage.get("cost")}
            for name, value in values.items():
                if type(value) in (int, float):
                    self.totals[name] += value
                else:
                    self.missing.add(name)

    def row(self):
        out = {"http_requests": self.requests}
        for name, value in self.totals.items():
            out[name] = "" if name in self.missing else (f"{value:.10f}" if name == "cost_usd" else value)
        return out


def status_of(row):
    return {"deal": "completed", "no_deal": "completed", "open": "turn_limit"}.get(row["outcome"], "crashed")


def load_inputs():
    base = json.loads((HERE / "config.json").read_text())
    configs = {e: effort_config(base, e) for e in EFFORTS}
    return configs, json.loads((ROOT / "scenarios.json").read_text())


def prepare_suite(suite, jobs, tasks, scenarios, configs):
    """Write or verify the manifest; open the suite CSV and return the recorded episodes."""
    sources = [HERE / "run_luna.py", HERE / "config.json", ROOT / "lab/experiment.py",
               ROOT / "pilot/transport.py", ROOT / "scenarios.json"]
    manifest = {"suite": suite, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                "configs": configs, "scenarios": scenarios, "max_turns": lab.MAX_TURNS, "repeats": 3,
                "efforts": list(EFFORTS), "jobs": jobs, "schedule_seed": SCHEDULE_SEED, "tasks": tasks,
                "unsettable": {"temperature": "not supported by the model; sent as null",
                               "top_p": "not supported by the model; sent as null", "seed": "not sent"},
                "reader": "same model and effort as the negotiators",
                "format": lab.FORMAT, "roles": lab.ROLE, "common": lab.COMMON, "reader_system": lab.READER_SYSTEM}
    out = HERE / "runs" / suite
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        current = json.loads(json.dumps(manifest))
        if any(old[k] != current[k] for k in current if k != "source_commit"):
            raise ValueError("Resume requires identical inputs and settings")
        manifest = old
    else:
        with manifest_path.open("x") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
    csv_path = out / "results.csv"
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != HEADER:
                raise ValueError("Unexpected results header")
            rows = list(reader)
        done = {(r["run"], r["scenario"]) for r in rows}
        if len(done) != len(rows):
            raise ValueError("Duplicate episode identity")
    else:
        done = set()
        with csv_path.open("x", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(HEADER)
    return manifest, csv_path, done


def run_task(suite, effort, condition, repeat, *, scenarios, config, key, manifest, csv_path, done, lock):
    """One run = one effort, one condition, one repeat, every scenario not yet in the CSV."""
    run_id = f"{suite}-{effort}-{condition}-{repeat:02d}"
    todo = [s for s in scenarios if (run_id, str(s["id"])) not in done]
    if not todo:
        return
    client = lab.OpenRouterClient(key, config)
    with (ROOT / "logs" / f"{run_id}.jsonl").open("a") as events, (ROOT / "logs" / f"{run_id}.txt").open("a") as console:
        active_scenario, meter = None, None
        def emit(event, **fields):
            if meter is not None:
                meter.observe(event, fields)
            record = {"time": datetime.now(timezone.utc).isoformat(), "run": run_id,
                      "scenario": active_scenario, "event": event, **fields}
            line = lab.redact(json.dumps(record, ensure_ascii=False, allow_nan=False), key)
            events.write(line + "\n"); events.flush()
            if event not in ("request", "response", "usage"):
                console.write(line + "\n"); console.flush()
            if event in ("episode_result", "crash", "http_error"):
                with lock:
                    print(line, flush=True)
        emit("setup", manifest=manifest, reasoning_effort=effort, condition=condition, repeat=repeat)
        for s in todo:
            active_scenario, meter = s["id"], UsageMeter()
            row = dict.fromkeys(HEADER, "")
            row.update(run=run_id, condition=condition, scenario=s["id"],
                       deal_possible=int(s["reserve"] <= s["budget"]), reasoning_effort=effort)
            emit("episode_start", systems={r: lab.system_prompt(r, s, condition) for r in ("buyer", "seller")})
            def call(role, messages, response_format):
                return client.complete(messages, emit, s["id"], role, response_format=response_format)
            started = time.monotonic()
            try:
                lab.negotiate(s, condition, call, emit, row)
            except Exception as exc:
                row.update(outcome="", price="", correct="", violation="",
                           note=f"crashed: {type(exc).__name__}: {lab.redact(str(exc), key)}")
                emit("crash", error=row["note"])
            row.update(status=status_of(row), elapsed_seconds=round(time.monotonic() - started, 3), **meter.row())
            emit("episode_result", result=row)
            with lock:
                with csv_path.open("a", newline="") as f:
                    csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n").writerow(row)
                done.add((run_id, str(s["id"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=SUITE)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.suite):
        parser.error("suite must be a simple identifier")
    configs, scenarios = load_inputs()
    tasks = [(e, c, r) for e in EFFORTS for c in lab.CONDITIONS for r in range(1, 4)]
    random.Random(SCHEDULE_SEED).shuffle(tasks)
    manifest, csv_path, done = prepare_suite(args.suite, args.jobs, tasks, scenarios, configs)
    key, lock = lab.read_key(args.env_file), threading.Lock()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_task, args.suite, *task, scenarios=scenarios, config=configs[task[0]], key=key,
                               manifest=manifest, csv_path=csv_path, done=done, lock=lock) for task in tasks]
        for future in futures:
            future.result()
    print(f"Completed suite {args.suite}: {len(done)} episodes", flush=True)


if __name__ == "__main__":
    main()
