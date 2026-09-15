"""Week 02 starter — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3]

Reads the task and the success criterion from TASK.md, runs each harness
--runs times, judges every run, appends one line per run to results.csv,
and saves each run's console output under logs/. Failed runs are kept:
they are data.
"""
import argparse
import csv
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from harness_plan_execute import run_plan_execute
from harness_react import run_react
from tools_shared import Meter, MODEL, PROVIDER, TOOL_SPECS

HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]


class Tee:
    def __init__(self, console, capture):
        self.console, self.capture = console, capture

    def write(self, text):
        self.console.write(text)
        self.capture.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.console.flush()
        self.capture.flush()


def next_run_number():
    last = 0
    if Path("results.csv").exists():
        with open("results.csv", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            if next(reader, None) != HEADER:
                raise ValueError("existing results.csv has an unexpected header")
            for row in reader:
                if row:
                    last = max(last, int(row[0]))
    for path in Path("logs").glob("*.txt"):
        match = re.fullmatch(r"(?:react|plan_exec)-(\d+)\.txt", path.name)
        if match:
            last = max(last, int(match.group(1)))
    return last + 1


def metadata():
    files = sorted(Path(".").glob("*.py")) + [Path("app.log"), Path("TASK.md")]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], text=True,
                            capture_output=True, timeout=10, check=True)
    info = {"provider": PROVIDER, "model": MODEL, "python": sys.version,
            "python_executable": sys.executable, "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
            "commit": commit.stdout.strip(), "tool_specs": TOOL_SPECS,
            "file_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            "react_max_steps": 8, "plan_max_replan": 1, "plan_max_tool_rounds": 3}
    if PROVIDER == "codex":
        from codex_backend import backend_info
        info.update(backend_info())
    return info


def run_one(run_no, name, fn, task, expected, settings):
    meter = Meter()
    note = ""
    with Path("logs", f"{name}-{run_no:02d}.txt").open("x", encoding="utf-8") as capture:
        with redirect_stdout(Tee(sys.stdout, capture)), redirect_stderr(Tee(sys.stderr, capture)):
            print("[config] " + json.dumps(settings, sort_keys=True))
            print("[run] " + json.dumps({"run": run_no, "harness": name,
                  "started_utc": datetime.now(timezone.utc).isoformat(), "task": task}))
            t0 = time.monotonic()
            try:
                out = fn(task, log=print, meter=meter)
                answer = out[0]
                if name == "plan_exec":
                    note = f"replans={out[2]}"
            except Exception as e:
                answer = ""
                note = f"crash: {type(e).__name__}: {e}"
                print(note)
            success = judge(answer, expected)
            print("[answer-json] " + json.dumps(answer))
            print(f"[final] {answer}")
            print(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                  f"({time.monotonic() - t0:.1f}s)")
            print("[meter] " + json.dumps(vars(meter), sort_keys=True))
    if not meter.tokens_complete:
        note += "; token usage incomplete (partial total in log)"
    return [run_no, name, "O" if success else "X",
            meter.tokens if meter.tokens_complete else "", meter.iters,
            meter.interventions, note]


def read_task(path="TASK.md"):
    text = Path(path).read_text(encoding="utf-8")
    task = re.search(r"^task:\s*(.+)$", text, flags=re.M)
    expected = re.search(r"^expected:\s*(.+)$", text, flags=re.M)
    if not task or not expected:
        raise SystemExit("TASK.md needs a 'task:' line and an 'expected:' line")
    return task.group(1).strip(), expected.group(1).strip()


def judge(answer: str, expected: str) -> bool:
    """Success = the expected string appears in the final answer. Fix the
    criterion in TASK.md before running; do not loosen it afterwards."""
    return expected.lower() in (answer or "").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    if args.runs < 1:
        ap.error("--runs must be positive")

    os.chdir(Path(__file__).resolve().parent)
    task, expected = read_task()
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    run_no = next_run_number()
    settings = metadata()

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for name, fn in (("react", run_react), ("plan_exec", run_plan_execute)):
            for _ in range(args.runs):
                row = run_one(run_no, name, fn, task, expected, settings)
                w.writerow(row)
                f.flush()
                run_no += 1
    print("\nresults.csv updated;", os.path.abspath("results.csv"))


if __name__ == "__main__":
    main()
