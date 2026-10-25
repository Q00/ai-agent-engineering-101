"""Append-only live experiment runner. No fake/mock execution mode."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import codex_backend
from contract_net import CONDITIONS, HEADER, ROOT, load_json, run_round

FROZEN_FILES = ("tasks.json", "prompts.json", "DESIGN.md", "contract_net.py",
                "codex_backend.py", "run_experiment.py")


def provenance():
    if os.environ.get("CONDA_DEFAULT_ENV") != "base":
        raise RuntimeError("Run in existing conda base, without changing environments")
    for name in FROZEN_FILES:
        subprocess.run(["git", "ls-files", "--error-unmatch", name], cwd=ROOT,
                       capture_output=True, check=True)
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--"] +
                                    list(FROZEN_FILES), cwd=ROOT, text=True)
    if dirty:
        raise RuntimeError("Commit experiment inputs/code before live calls: " + dirty)
    return dict(git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"],
                cwd=ROOT, text=True).strip(), python=sys.version, executable=sys.executable,
                conda_env=os.environ["CONDA_DEFAULT_ENV"],
                hashes={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in FROZEN_FILES})


def read_rows(directory):
    path = directory / "results.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(HEADER):
            raise RuntimeError("unexpected existing CSV header")
        return list(reader)


def append_row(directory, row):
    path = directory / "results.csv"
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def execute_run(directory, run_id, condition, tasks, prompts, info, call_model):
    log_dir = directory / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "{}-{}.log".format(run_id, condition)
    row = dict.fromkeys(HEADER, "")
    row.update(run=run_id, condition=condition)
    started = time.monotonic()
    failure = None
    with log_path.open("x", encoding="utf-8") as handle:
        def emit(event, **data):
            line = json.dumps(dict(event=event, **data), ensure_ascii=True)
            handle.write(line + "\n")
            handle.flush()
            if event in ("config", "announcement", "bid", "award", "unassigned",
                         "evaluation", "summary", "crash", "finished"):
                print(line, flush=True)

        emit("config", run=run_id, condition=condition,
             started_at=datetime.now(timezone.utc).isoformat(), **info)
        try:
            counts, stats = run_round(tasks, condition, prompts, call_model, emit)
            row.update(counts)
            note = dict(stats, status="completed")
        except (Exception, KeyboardInterrupt) as exc:
            failure = exc
            note = dict(status="crashed", error=type(exc).__name__ + ": " + str(exc))
            emit("crash", **note)
        note["wall_seconds"] = round(time.monotonic() - started, 3)
        row["note"] = json.dumps(note, sort_keys=True)
        emit("finished", row=row)
    append_row(directory, row)
    return failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3,
                        help="target successful repetitions per condition; resume appends")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    rows = read_rows(ROOT)
    logs = ROOT / "logs"
    expected_logs = {"{}-{}.log".format(row["run"], row["condition"]) for row in rows}
    actual_logs = {p.name for p in logs.glob("*.log")} if logs.exists() else set()
    if expected_logs != actual_logs:
        raise RuntimeError("CSV/log mismatch; retain and reconcile interrupted evidence first")
    completed = Counter(row["condition"] for row in rows if row["correct"] != "")
    if all(completed[c] >= args.repetitions for c in CONDITIONS):
        print("Requested completed runs already exist; nothing overwritten.")
        return
    info = dict(codex_backend.backend_info(), **provenance())
    tasks, prompts = load_json("tasks.json"), load_json("prompts.json")
    next_id = max([int(row["run"]) for row in rows] or [0]) + 1
    for repetition in range(args.repetitions):
        for condition in CONDITIONS:
            if completed[condition] > repetition:
                continue
            run_id = "{:03d}".format(next_id)
            failure = execute_run(ROOT, run_id, condition, tasks, prompts, info,
                                  codex_backend.complete)
            next_id += 1
            if failure is not None:
                raise SystemExit("Run {} retained as crashed; diagnose before resuming: {}"
                                 .format(run_id, failure))
            completed[condition] += 1


if __name__ == "__main__":
    main()
