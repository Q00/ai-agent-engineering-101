"""Edited from Week 02 starter — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3]

Reads the task and the success criterion from TASK.md, runs each harness
--runs times, judges every run, appends one line per run to results.csv,
and saves each run's console output under logs/. Failed runs are kept:
they are data.
"""
import argparse
import csv
import os
import re
import time
from pathlib import Path

from harness_plan_execute import (run_plan_execute, run_plan_execute_version_edited)
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

EXPERIMENTS = (
    (
        "a",
        (
            ("react", run_react, False),
            ("plan_exec", run_plan_execute, True),
        ),
    ),
    (
        "b",
        (
            ("react", run_react, False),
            (
                "plan_exec_edited",
                run_plan_execute_version_edited,
                True,
            ),
        ),
    ),
)


def get_last_run_no(results_path: Path) -> int:
    """Return the largest saved run number, tolerating blank rows."""
    if not results_path.exists() or results_path.stat().st_size == 0:
        return 0

    with results_path.open(newline="", encoding="utf-8") as file:
        rows = csv.reader(file)
        next(rows, None)  # header
        return max(
            (
                int(row[0])
                for row in rows
                if row and row[0].strip().isdigit()
            ),
            default=0,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    task, expected = read_task()
    logs_path = Path("logs")
    results_path = Path("results.csv")
    logs_path.mkdir(exist_ok=True)

    new_file = not results_path.exists() or results_path.stat().st_size == 0
    run_no = get_last_run_no(results_path)

    with results_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if new_file:
            writer.writerow(HEADER)

        # Interleave the four runners within each trial. This avoids running all
        # samples of one method first and makes temporal comparisons fairer.
        for _trial in range(1, args.runs + 1):
            for group, runners in EXPERIMENTS:
                for method, fn, reports_replans in runners:
                    run_no += 1
                    result_name = f"{group}_{method}"
                    lines: list[str] = []

                    def log(message, _lines=lines):
                        print(message)
                        _lines.append(str(message))

                    started_at = time.perf_counter()
                    note = ""

                    try:
                        output = fn(task, log=log)
                        answer, meter = output[0], output[1]

                        if reports_replans:
                            if len(output) < 3:
                                raise ValueError(
                                    f"{fn.__name__} must return "
                                    "(answer, meter, replans)"
                                )
                            note = f"replans={output[2]}"

                    except Exception as error:
                        answer = ""
                        meter = None
                        note = (
                            f"crash: {type(error).__name__}: {error}"
                        )
                        log(note)

                    elapsed = time.perf_counter() - started_at
                    success = judge(answer, expected)
                    log(f"[final] {answer.strip()[:300]}")
                    log(
                        f"[judge] expected={expected!r} -> "
                        f"{'O' if success else 'X'} ({elapsed:.1f}s)"
                    )

                    log_file = logs_path / f"{result_name}-{run_no:02d}.txt"
                    log_file.write_text(
                        "\n".join(lines) + "\n",
                        encoding="utf-8",
                    )

                    writer.writerow(
                        [
                            run_no,
                            result_name,
                            "O" if success else "X",
                            meter.tokens if meter is not None else "",
                            meter.iters if meter is not None else "",
                            (
                                meter.interventions
                                if meter is not None
                                else ""
                            ),
                            note,
                        ]
                    )
                    file.flush()

    total_runs = args.runs * sum(
        len(runners) for _, runners in EXPERIMENTS
    )
    print(
        f"\nresults.csv updated with {total_runs} runs; "
        f"{os.path.abspath(results_path)}"
    )



if __name__ == "__main__":
    main()