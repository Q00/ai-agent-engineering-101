"""Week 02 — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3] [--workers 6]

Reads the task and the success criterion from TASK.md, runs each harness
--runs times, judges every run, appends one line per run to results.csv,
and saves each run's console output under logs/. Failed runs are kept:
they are data.

Parallelism is a runner detail, not an experimental variable. Each run builds
its own Chat and its own Meter, so runs share no state and the four metrics are
unaffected by how many run at once. --workers 1 reproduces the original
sequential behaviour exactly. Per-run output is buffered and written to
logs/<harness>-<nn>.txt after the run finishes, so concurrent runs never
interleave inside a log file, and rows are written to results.csv in run-number
order after every run completes.
"""
import argparse
import csv
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness_plan_execute import run_plan_execute
from harness_react import run_react

HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]


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


def one_run(run_no: int, name: str, fn, task: str, expected: str) -> dict:
    """Execute a single run in isolation and return its row plus its log text.

    Nothing is printed here: the lines are buffered so that runs executing at
    the same time cannot interleave in a log file or on the console.
    """
    lines = []

    def log(msg, _lines=lines):
        _lines.append(str(msg))

    t0 = time.time()
    note = ""
    try:
        out = fn(task, log=log)
        answer, meter = out[0], out[1]
        if name == "plan_exec":
            note = f"replans={out[2]}"
    except Exception as e:                # a crash is a failed run, not a lost run
        answer, meter, note = "", None, f"crash: {type(e).__name__}: {e}"
        log(note)
    elapsed = time.time() - t0
    success = judge(answer, expected)
    log(f"[final] {answer.strip()[:300]}")
    log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} ({elapsed:.1f}s)")

    Path("logs", f"{name}-{run_no:02d}.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    return {
        "run_no": run_no,
        "name": name,
        "elapsed": elapsed,
        "row": [run_no, name, "O" if success else "X",
                meter.tokens if meter else "",
                meter.iters if meter else "",
                meter.interventions if meter else "", note],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3,
                    help="runs per harness (default 3)")
    ap.add_argument("--workers", type=int, default=6,
                    help="runs executed concurrently; 1 = sequential (default 6)")
    args = ap.parse_args()

    task, expected = read_task()
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    offset = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            offset = sum(1 for _ in f) - 1

    # Run numbers are assigned up front so they do not depend on completion
    # order: react takes the first block, plan_exec the second, exactly as the
    # sequential runner numbered them.
    jobs = []
    run_no = offset
    for name, fn in (("react", run_react), ("plan_exec", run_plan_execute)):
        for _ in range(args.runs):
            run_no += 1
            jobs.append((run_no, name, fn))

    workers = max(1, min(args.workers, len(jobs)))
    print(f"{len(jobs)} run(s), {workers} at a time, model={os.environ.get('AGENT_MODEL', 'default')}")

    wall0 = time.time()
    if workers == 1:
        results = [one_run(n, nm, fn, task, expected) for n, nm, fn in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one_run, n, nm, fn, task, expected)
                       for n, nm, fn in jobs]
            results = [f.result() for f in futures]
    wall = time.time() - wall0

    results.sort(key=lambda r: r["run_no"])
    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for r in results:
            w.writerow(r["row"])

    for r in results:
        print(f"  run {r['run_no']:>2} {r['name']:<10} "
              f"{'O' if r['row'][2] == 'O' else 'X'}  "
              f"tokens={r['row'][3]:<7} iters={r['row'][4]:<3} "
              f"{r['elapsed']:.1f}s  {r['row'][6]}")
    serial = sum(r["elapsed"] for r in results)
    print(f"\nwall {wall:.1f}s (sum of runs {serial:.1f}s, speedup {serial / wall:.1f}x)")
    print("results.csv updated;", os.path.abspath("results.csv"))


if __name__ == "__main__":
    main()
