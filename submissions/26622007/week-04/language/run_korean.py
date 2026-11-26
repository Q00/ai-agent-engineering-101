"""Run the Korean translation for both turn policies; retain every attempt."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import re
import subprocess
import threading
import time

import korean as ko

HERE, ROOT, lab = ko.HERE, ko.ROOT, ko.lab
HEADER = ko.unlimited.HEADER + ["language", "turn_policy"]
SUITE = "korean-deepseek-20260922"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=SUITE)
    parser.add_argument("--env-file", type=ko.Path)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.suite): parser.error("Invalid suite")
    config = json.loads((ROOT / "lab/config.json").read_text())
    config["max_http_requests"] = None
    scenarios = [{**s, "item": ko.ITEMS[s["id"]]} for s in json.loads((ROOT / "scenarios.json").read_text())]
    tasks = [(p, c, r) for p in ("8", "none") for c in lab.CONDITIONS for r in range(1, 4)]
    random.Random(20260922).shuffle(tasks)
    sources = [HERE / "run_korean.py", HERE / "korean.py", HERE / "ANALYSIS_PLAN.md", ROOT / "lab/experiment.py",
               ROOT / "turn_limit/run_unlimited.py", ROOT / "pilot/transport.py", ROOT / "scenarios.json", ROOT / "lab/config.json"]
    manifest = {"suite": args.suite, "language": "ko", "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "config": config, "scenarios": scenarios, "max_turns": [8, None], "repeats": 3, "jobs": args.jobs,
        "observation_seconds": 180, "schedule_seed": 20260922, "tasks": tasks,
        "format": lab.FORMAT, "roles": lab.ROLE, "common": lab.COMMON, "reader_system": lab.READER_SYSTEM}
    out = HERE / "runs" / args.suite
    out.mkdir(parents=True, exist_ok=True)
    path = out / "manifest.json"
    if path.exists():
        old = json.loads(path.read_text())
        current = json.loads(json.dumps(manifest))
        if any(old[k] != current[k] for k in current if k != "source_commit"):
            raise ValueError("Resume requires identical inputs and settings")
        manifest = old
    else:
        with path.open("x") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2); f.write("\n")
    csv_path = out / "results.csv"
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == HEADER
            rows = list(reader)
        done = {(r["run"], r["scenario"]) for r in rows}
        assert len(done) == len(rows)
    else:
        done = set()
        with csv_path.open("x", newline="") as f: csv.writer(f, lineterminator="\n").writerow(HEADER)
    key, lock = lab.read_key(args.env_file), threading.Lock()
    def run(policy, condition, repeat):
        run_id = f"{args.suite}-{policy}-{condition}-{repeat:02d}"
        todo = [s for s in scenarios if (run_id, str(s["id"])) not in done]
        if not todo: return
        client = lab.OpenRouterClient(key, {**config, "max_http_requests": math.inf})
        with (ROOT / "logs" / f"{run_id}.jsonl").open("a") as events, (ROOT / "logs" / f"{run_id}.txt").open("a") as console:
            active_scenario = None
            def emit(event, **fields):
                record = {"time": datetime.now(timezone.utc).isoformat(), "run": run_id,
                          "scenario": active_scenario, "event": event, **fields}
                line = lab.redact(json.dumps(record, ensure_ascii=False, allow_nan=False), key)
                events.write(line + "\n"); events.flush()
                if event not in ("request", "response", "usage"):
                    console.write(line + "\n"); console.flush()
                if event in ("episode_result", "crash", "observation_censored", "http_error"):
                    with lock: print(line, flush=True)
            emit("setup", manifest=manifest, turn_policy=policy, condition=condition, repeat=repeat)
            for s in todo:
                active_scenario = s["id"]
                row = dict.fromkeys(HEADER, "")
                row.update(run=run_id, condition=condition, scenario=s["id"], deal_possible=int(s["reserve"] <= s["budget"]),
                           language="ko", turn_policy=policy)
                emit("episode_start", systems={r: lab.system_prompt(r, s, condition) for r in ("buyer", "seller")})
                def call(role, messages, response_format):
                    return client.complete(messages, emit, s["id"], role, response_format=response_format)
                started = time.monotonic()
                try:
                    if policy == "8":
                        lab.negotiate(s, condition, call, emit, row)
                        row["status"] = "completed" if row["outcome"] != "open" else "turn_limit"
                    else:
                        ko.unlimited.negotiate(s, condition, call, emit, row, 180)
                except Exception as exc:
                    row.update(outcome="", price="", correct="", violation="", status="crashed",
                               note=f"crashed: {type(exc).__name__}: {lab.redact(str(exc), key)}")
                    emit("crash", error=row["note"])
                row["elapsed_seconds"] = round(time.monotonic() - started, 3)
                emit("episode_result", result=row)
                with lock:
                    with csv_path.open("a", newline="") as f: csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n").writerow(row)
                    done.add((run_id, str(s["id"])))
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run, *task) for task in tasks]
        for future in futures: future.result()
    print(f"Completed Korean suite {args.suite}: {len(done)} episodes", flush=True)


if __name__ == "__main__": main()
