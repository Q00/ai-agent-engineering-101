"""Week 03 runner -- executes the three conditions and writes results.csv
and logs/ per weeks/week-03/README.md.

Usage:
    python run_lab.py                      # 3 runs per condition (default)
    python run_lab.py --runs 5
    python run_lab.py --condition baseline --runs 3
"""
import argparse
import csv
import sys
import time
from pathlib import Path

from contract_net import (MODEL, PROVIDER, TEMPERATURE, build_contractors,
                           load_tasks, run_round)

HERE = Path(__file__).parent
CONDITIONS = ("baseline", "homogeneous", "overconfident")
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]


def run_one(condition: str, run_index: int, tasks, results_path: Path, log_dir: Path):
    log_lines = []

    def log(line: str):
        print(line)
        log_lines.append(line)

    log_name = f"{condition}-{run_index:02d}.txt"
    log(f"=== contract net run: condition={condition} run={run_index} "
        f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE} ===")

    row = {"run": run_index, "condition": condition, "note": ""}
    try:
        contractors = build_contractors(condition)
        metrics = run_round(tasks, contractors, log)
        row.update(tasks=metrics["tasks"], correct=metrics["correct"],
                    messages=metrics["messages"], unassigned=metrics["unassigned"],
                    misawards=metrics["misawards"])
        log(f"=== done: tokens={metrics['tokens']} iters={metrics['iters']} ===")
    except Exception as e:  # crashed run: keep the row, blank the counts, log the error
        row.update(tasks="", correct="", messages="", unassigned="", misawards="")
        row["note"] = f"crashed: {type(e).__name__}: {e}"
        log(f"=== CRASHED: {type(e).__name__}: {e} ===")

    (log_dir / log_name).write_text("\n".join(log_lines), encoding="utf-8")

    with results_path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([row[k] for k in HEADER])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--condition", choices=CONDITIONS, default=None,
                     help="run only this condition (default: all three)")
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    results_path = HERE / "results.csv"
    log_dir = HERE / "logs"
    log_dir.mkdir(exist_ok=True)

    if not results_path.exists():
        with results_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADER)

    conditions = [args.condition] if args.condition else list(CONDITIONS)
    run_index = 1
    for condition in conditions:
        for _ in range(args.runs):
            run_one(condition, run_index, tasks, results_path, log_dir)
            run_index += 1
            time.sleep(1)  # be polite to rate limits


if __name__ == "__main__":
    sys.exit(main())
