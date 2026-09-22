"""Run the additional one-axis Plan-then-Execute experiments.

Usage: python3 run_plan_variants.py --runs 3

Results are appended to experiment_results.csv and console captures are saved
under logs/. Baseline results.csv and its logs are never modified.
"""
import argparse
import csv
import os
import time
from pathlib import Path

from harness_plan_execute import (
    run_plan_execute,
    run_plan_execute_no_replan,
    run_plan_execute_short_plan,
)
from run_ab import judge, read_task

HEADER = [
    "run", "variant", "success", "tokens", "iters", "interventions",
    "off_plan", "replans", "note",
]
VARIANTS = (
    ("baseline_replan_1", run_plan_execute),
    ("short_plan_max_3", run_plan_execute_short_plan),
    ("no_replan_0", run_plan_execute_no_replan),
)


def next_run_number(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        return sum(1 for row in csv.reader(handle) if row) - 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    task, expected = read_task()
    result_path = Path("experiment_results.csv")
    new_file = not result_path.exists()
    run_no = next_run_number(result_path)
    Path("logs").mkdir(exist_ok=True)

    with result_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(HEADER)

        for variant, run in VARIANTS:
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(message, captured=lines):
                    print(message)
                    captured.append(str(message))

                started = time.time()
                note = ""
                try:
                    answer, meter, replans = run(task, log=log)
                except Exception as error:
                    answer, meter, replans = "", None, 0
                    note = f"crash: {type(error).__name__}: {error}"
                    log(note)

                success = judge(answer, expected)
                off_plan = sum("OFF_PLAN" in line for line in lines)
                log(f"[final] {answer.strip()[:300]}")
                log(
                    f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                    f"off_plan={off_plan} replans={replans} "
                    f"({time.time() - started:.1f}s)"
                )
                Path("logs", f"variant-{variant}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8"
                )
                writer.writerow([
                    run_no, variant, "O" if success else "X",
                    meter.tokens if meter else "",
                    meter.iters if meter else "",
                    meter.interventions if meter else "",
                    off_plan, replans, note,
                ])
                handle.flush()

    print(f"\nexperiment_results.csv updated; {os.path.abspath(result_path)}")


if __name__ == "__main__":
    main()
