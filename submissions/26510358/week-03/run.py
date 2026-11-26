"""Run each condition three times; append results and preserve raw console logs."""
import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from contractor import CONDITIONS, make_contractors
from manager import RoundResult, run_round
from model import Chat, Meter, Settings

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]


def load_tasks(path):
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or len(tasks) < 5:
        raise ValueError("Expected at least five tasks")
    ids = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"id", "desc", "gold"}:
            raise ValueError("Each task requires id, desc, and gold")
        if type(task["id"]) is not int or task["id"] in ids:
            raise ValueError("Task ids must be unique integers")
        if not isinstance(task["desc"], str) or not task["desc"].strip() or task["gold"] not in ("A", "B", "C"):
            raise ValueError("Invalid task description or gold")
        ids.add(task["id"])
    if {task["gold"] for task in tasks} != set("ABC"):
        raise ValueError("The task set must cover A, B, and C")
    return tasks


def next_run_id(rows, log_dir):
    ids = [int(row["run"]) for row in rows]
    # Preserve orphaned logs too, e.g. after a process was killed before CSV append.
    for path in log_dir.glob("*.txt"):
        match = re.fullmatch(r"(?:baseline|homogeneous|overconfident)-(\d+)\.txt", path.name)
        if match:
            ids.append(int(match.group(1)))
    return max(ids, default=0) + 1


def safe_error(exc):
    message = f"{type(exc).__name__}: {exc}"
    for name, value in os.environ.items():
        if value and any(marker in name for marker in ("API_KEY", "TOKEN", "SECRET")):
            message = message.replace(value, "[REDACTED]")
    return message


def run_one(condition, run_id, tasks, metadata, call_model, output, results_file):
    meter = Meter()
    result = RoundResult(tasks=len(tasks))
    team = make_contractors(condition)
    row = dict.fromkeys(HEADER, "")
    row.update(run=run_id, condition=condition)
    error = None
    interrupted = False
    log_name = f"{condition}-{run_id:02d}.txt"
    with (output / "logs" / log_name).open("x", encoding="utf-8") as capture:
        def log(event, **data):
            line = f"[{event}] " + json.dumps(data, ensure_ascii=False, allow_nan=False)
            capture.write(line + "\n")
            capture.flush()
            print(line, flush=True)

        log("settings", **metadata, run=run_id, condition=condition,
            started_utc=datetime.now(timezone.utc).isoformat())
        log("tasks", tasks=tasks)
        for contractor in team:
            log("system", contractor=contractor.name, content=contractor.system)
        try:
            run_round(tasks, team, meter, call_model, log, result)
            row.update({key: getattr(result, key) for key in HEADER[2:7]})
        except (Exception, KeyboardInterrupt) as exc:
            error = safe_error(exc)
            interrupted = isinstance(exc, KeyboardInterrupt)
            log("error", error=error, partial=asdict(result))
        finally:
            note = dict(status="crashed" if error else "complete", parse_fails=result.parse_fails,
                        calls=meter.calls, replies=meter.replies, tokens=meter.tokens,
                        input_tokens=meter.input_tokens, output_tokens=meter.output_tokens,
                        completed_tasks=result.completed_tasks, log=f"logs/{log_name}",
                        task_sha256=metadata["task_sha256"])
            if error:
                note["error"] = error
            row["note"] = json.dumps(note, ensure_ascii=False)
            log("summary", **row, usage=asdict(meter))
            csv.DictWriter(results_file, fieldnames=HEADER).writerow(row)
            results_file.flush()
            os.fsync(results_file.fileno())
    if interrupted:
        raise KeyboardInterrupt
    return error is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="Repetitions per condition")
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--tasks", type=Path, default=HERE / "tasks.json")
    parser.add_argument("--output", type=Path, default=HERE)
    parser.add_argument("--env-file", type=Path, help="Optional local dotenv; never logged or committed")
    parser.add_argument("--check", action="store_true", help="Validate settings without API calls or output files")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        load_dotenv(args.env_file, override=False)
    tasks = load_tasks(args.tasks)
    settings = Settings.from_env()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, text=True,
                              capture_output=True, check=False).stdout.strip() or "unknown"
    metadata = dict(settings.metadata(), git_commit=revision,
                    task_sha256=hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
                    order="A,B,C", conditions=list(CONDITIONS))
    if args.check:
        print(json.dumps(dict(metadata, tasks=len(tasks), key_available=True), indent=2))
        return 0
    call_model = Chat(settings)
    (args.output / "logs").mkdir(parents=True, exist_ok=True)
    with (args.output / "results.csv").open("a+", newline="", encoding="utf-8") as results_file:
        # Prevent concurrent runners from reusing an id or interleaving CSV rows.
        fcntl.flock(results_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        results_file.seek(0)
        reader = csv.DictReader(results_file)
        rows = list(reader)
        if reader.fieldnames not in (None, HEADER):
            raise ValueError("Existing results.csv header does not match the lab")
        results_file.seek(0, os.SEEK_END)
        if reader.fieldnames is None:
            csv.DictWriter(results_file, fieldnames=HEADER).writeheader()
            results_file.flush()
        run_id = next_run_id(rows, args.output / "logs")
        conditions = [args.condition] if args.condition else CONDITIONS
        for condition in conditions:
            for _ in range(args.runs):
                if not run_one(condition, run_id, tasks, metadata, call_model, args.output, results_file):
                    return 1  # Preserve the failure and stop; do not silently retry.
                run_id += 1
    return 0


def on_termination(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, on_termination)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
