"""Week 03 — run the contract-net experiment and record results.csv.

Usage: python run_experiment.py [--runs 3]

Loads tasks.json, runs each of the three conditions --runs times on the same
task set, appends one row per run to results.csv, and saves each run's full
announce/bid/award transcript under logs/. A crashed run stays in results.csv
with blank counts and the error in `note` — it is not silently dropped.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

from contract_net import CONTRACTORS, OVERCONFIDENT_CONTRACTOR, run_condition

HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
CONDITIONS = ["baseline", "homogeneous", "overconfident"]


def load_tasks(path="tasks.json"):
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    golds = {t["gold"] for t in tasks}
    bad = golds - set(CONTRACTORS)
    if bad:
        raise SystemExit(f"tasks.json uses gold contractor(s) not in CONTRACTORS: {bad}")
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tasks = load_tasks()
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    run_no = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            run_no = sum(1 for _ in f) - 1

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for condition in CONDITIONS:
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                log(f"=== run {run_no}: condition={condition} "
                    f"(overconfident contractor: {OVERCONFIDENT_CONTRACTOR}) ===")
                t0 = time.time()
                note = ""
                metrics = None
                try:
                    metrics, meter = run_condition(condition, tasks, log)
                    note = f"tokens={meter.tokens} calls={meter.calls}"
                except Exception as e:  # a crash is a failed run, not a lost run
                    note = f"crash: {type(e).__name__}: {e}"
                    log(note)
                log(f"[done] run {run_no} in {time.time() - t0:.1f}s")

                Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")

                if metrics is None:
                    w.writerow([run_no, condition, "", "", "", "", "", note])
                else:
                    w.writerow([run_no, condition, metrics["tasks"], metrics["correct"],
                                metrics["messages"], metrics["unassigned"],
                                metrics["misawards"], note])
                f.flush()
    print("\nresults.csv updated;", Path("results.csv").resolve())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp949
    main()
