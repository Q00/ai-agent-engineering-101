"""Week 03 — run baseline/homogeneous/overconfident conditions and record results.csv.

Usage: python run_experiment.py [--runs 3]

Reads tasks.json, runs each condition --runs times, appends one line per run
to results.csv, and saves each run's console output under logs/. Crashed
runs are kept with blank counts and the error in note (they are data too).
"""
import argparse
import csv
import json
from pathlib import Path

from manager import make_team, run_round
from tools_shared import Meter

HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
CONDITIONS = ["baseline", "homogeneous", "overconfident"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tasks = json.load(open("tasks.json", encoding="utf-8"))
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

                note = ""
                try:
                    team = make_team(condition)
                    meter = Meter()
                    result = run_round(tasks, team, meter, log=log)
                    if result.parse_fails:
                        note = f"parse_fails={result.parse_fails}"
                    row = [run_no, condition, result.tasks, result.correct,
                           result.messages, result.unassigned, result.misawards, note]
                except Exception as e:               # a crash is a failed run, not a lost run
                    note = f"crash: {type(e).__name__}: {e}"
                    log(note)
                    row = [run_no, condition, "", "", "", "", "", note]

                log(f"[result] run={run_no} condition={condition} -> {row}")
                Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                w.writerow(row)
                f.flush()
    print("\nresults.csv updated;", Path("results.csv").resolve())


if __name__ == "__main__":
    main()
