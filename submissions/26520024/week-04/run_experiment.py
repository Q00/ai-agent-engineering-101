"""Append-only, resumable negotiation experiment. No simulated execution mode."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time

from codex_backend import ModelSession, backend_info
from negotiation import CONDITIONS, HEADER, MAX_TURNS, ROOT, load_json, negotiate

FROZEN = ("scenarios.json", "prompts.json", "DESIGN.md", "negotiation.py",
          "codex_backend.py", "run_experiment.py")


def provenance():
    if os.environ.get("CONDA_DEFAULT_ENV") != "base":
        raise RuntimeError("Use existing conda base")
    for name in FROZEN:
        subprocess.run(["git", "ls-files", "--error-unmatch", name], cwd=ROOT,
                       capture_output=True, check=True)
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--"] + list(FROZEN),
                                    cwd=ROOT, text=True)
    if dirty:
        raise RuntimeError("Commit experiment inputs/code before calls: " + dirty)
    return dict(git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                text=True).strip(), python=sys.version, executable=sys.executable,
                conda_env=os.environ["CONDA_DEFAULT_ENV"], max_turns=MAX_TURNS,
                hashes={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FROZEN})


def read_rows(root):
    path = root / "results.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(HEADER):
            raise RuntimeError("CSV header mismatch")
        rows = list(reader)
    pairs = [(row["run"], row["scenario"]) for row in rows]
    if len(pairs) != len(set(pairs)):
        raise RuntimeError("duplicate (run, scenario) rows")
    return rows


def append_row(root, row):
    if any((r["run"], r["scenario"]) == (row["run"], str(row["scenario"])) for r in read_rows(root)):
        raise RuntimeError("refusing duplicate episode")
    path = root / "results.csv"
    header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\n")
        if header:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


class Journal:
    def __init__(self, path):
        self.handle = path.open("a", encoding="utf-8")

    def emit(self, event, **data):
        line = json.dumps(dict(event=event, **data), ensure_ascii=True)
        self.handle.write(line + "\n")
        self.handle.flush()
        if event in ("config", "episode_start", "message", "reader_label", "parse",
                     "episode_result", "crash", "retry", "recovery", "run_complete"):
            print(line, flush=True)

    def sync(self):
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self):
        self.handle.close()


def crashed_row(run, condition, scenario, error, stats=None):
    row = dict.fromkeys(HEADER, "")
    row.update(run=run, condition=condition, scenario=str(scenario),
               note=json.dumps(dict(status="crashed", error=error, partial_stats=stats or {}), sort_keys=True))
    return row


def reconcile(root, run, condition, events, journal):
    rows = {(r["run"], r["scenario"]): r for r in read_rows(root)}
    finished = {}
    started = set()
    for event in events:
        if event["event"] == "episode_start":
            started.add(str(event["scenario"]))
        if event["event"] == "episode_result":
            scenario = str(event["scenario"])
            if scenario in finished:
                raise RuntimeError("duplicate episode results in log")
            finished[scenario] = event["row"]
    for scenario, row in finished.items():
        key = (run, scenario)
        if key not in rows:
            append_row(root, row)
            journal.emit("recovery", scenario=scenario, reason="restored completed log row to CSV")
        elif rows[key] != {k: str(v) for k, v in row.items()}:
            raise RuntimeError("log/CSV disagreement")
    for scenario in sorted(started - set(finished)):
        if (run, scenario) in rows:
            raise RuntimeError("CSV row without completed log evidence")
        row = crashed_row(run, condition, scenario, "Interrupted episode found during resume; partial log retained")
        journal.emit("recovery", scenario=scenario, reason="unfinished episode marked crashed")
        journal.emit("episode_result", scenario=scenario, row=row)
        journal.sync()
        append_row(root, row)


def run_group(root, run, condition, scenarios, prompts, info, session_factory=ModelSession):
    directory = root / "logs"
    directory.mkdir(exist_ok=True)
    path = directory / (run + ".log")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    if events:
        config = events[0]
        if config["event"] != "config" or config["run"] != run or config["condition"] != condition:
            raise RuntimeError("wrong log config")
        for key, value in info.items():
            if key != "git_commit" and config.get(key) != value:
                raise RuntimeError("configuration changed during resume: " + key)
    journal = Journal(path)
    try:
        if not events:
            journal.emit("config", run=run, condition=condition,
                         started_at=datetime.now(timezone.utc).isoformat(), **info)
        else:
            reconcile(root, run, condition, events, journal)
        recorded = {(r["run"], r["scenario"]) for r in read_rows(root)}
        for scenario in scenarios:
            sid = str(scenario["id"])
            if (run, sid) in recorded:
                continue
            def emit(event, **data):
                journal.emit(event, scenario=sid, **data)
            emit("episode_start", evaluation_scenario=scenario)
            model = session_factory(emit)
            started = time.monotonic()
            failure = None
            try:
                counts = negotiate(scenario, condition, prompts, model, emit)
                row = dict(run=run, condition=condition, scenario=sid, **counts)
                row["note"] = json.dumps(dict(model.stats, status="completed",
                    wall_seconds=round(time.monotonic() - started, 3)), sort_keys=True)
            except (Exception, KeyboardInterrupt) as exc:
                failure = exc
                error = type(exc).__name__ + ": " + str(exc)
                emit("crash", error=error, partial_stats=model.stats)
                row = crashed_row(run, condition, sid, error, model.stats)
            emit("episode_result", row=row)
            journal.sync()
            append_row(root, row)
            if failure is not None:
                raise RuntimeError("crashed episode retained; diagnose before resume: " + str(failure))
        journal.emit("run_complete", run=run, condition=condition)
        journal.sync()
    finally:
        journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    with (ROOT / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scenarios, prompts = load_json("scenarios.json"), load_json("prompts.json")
        recorded = {(r["run"], r["scenario"]) for r in read_rows(ROOT)}
        plan = [("{}-{:02d}".format(c, i), c) for i in range(1, args.repetitions + 1) for c in CONDITIONS]
        todo = [(run, c) for run, c in plan if any((run, str(s["id"])) not in recorded for s in scenarios)]
        if not todo:
            print("All requested (run, scenario) pairs already recorded; no model calls or log changes.")
            return
        info = dict(backend_info(), **provenance())
        for run, condition in todo:
            run_group(ROOT, run, condition, scenarios, prompts, info)


if __name__ == "__main__":
    main()
