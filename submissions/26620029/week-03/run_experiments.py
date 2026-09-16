"""Runner: three conditions x N runs each (default 3), same task set.

Writes one row per run to results.csv and one transcript per run to logs/.
A crashed run keeps its row, with blank counts and the error in the note
column, per the week-03 data contract.

Usage:
    python run_experiments.py [--runs N]
"""
import argparse
import csv
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from contract_net import CONDITIONS, run_condition

HERE = Path(__file__).parent
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="runs per condition")
    args = parser.parse_args()

    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    (HERE / "logs").mkdir(exist_ok=True)

    rows = []
    run_number = 0
    for condition in CONDITIONS:
        for i in range(1, args.runs + 1):
            run_number += 1
            log_path = HERE / "logs" / f"{condition}-run{i}.txt"
            lines = [f"=== run {run_number} ({condition}), {datetime.now().isoformat()} ==="]

            def log(msg, _lines=lines):
                _lines.append(msg)
                print(msg)

            try:
                totals = run_condition(condition, tasks, log)
                log(f"=== summary: tasks={totals['tasks']} correct={totals['correct']} "
                    f"messages={totals['messages']} unassigned={totals['unassigned']} "
                    f"misawards={totals['misawards']} ===")
                rows.append([run_number, condition, totals["tasks"], totals["correct"],
                             totals["messages"], totals["unassigned"], totals["misawards"], ""])
            except Exception as e:
                log(f"=== CRASHED: {e} ===")
                lines.append(traceback.format_exc())
                rows.append([run_number, condition, "", "", "", "", "", f"crashed: {e}"])

            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    results_path = HERE / "results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)

    print(f"\nwrote {len(rows)} run(s) to {results_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
