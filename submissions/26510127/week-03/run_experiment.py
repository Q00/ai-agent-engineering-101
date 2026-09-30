"""
Run the Week 03 Contract Net experiment.

Runs baseline, homogeneous, and overconfident conditions three times each.
Each run produces one row in results.csv and one log file.
"""

import csv
import json
import traceback
from pathlib import Path

from contract_net import MODEL, PROVIDER, TEMPERATURE, run_round


BASE_DIR = Path(__file__).resolve().parent
TASKS_PATH = BASE_DIR / "tasks.json"
RESULTS_PATH = BASE_DIR / "results.csv"
LOGS_DIR = BASE_DIR / "logs"

CONDITIONS = [
    "baseline",
    "homogeneous",
    "overconfident",
]

REPEATS = 3

FIELDNAMES = [
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
]


def load_tasks() -> list[dict]:
    """Load the task set fixed before the experiment."""

    with TASKS_PATH.open(encoding="utf-8") as file:
        tasks = json.load(file)

    if not isinstance(tasks, list):
        raise ValueError("tasks.json must contain a JSON list")

    return tasks


def append_result(row: dict) -> None:
    """Append one completed or crashed run to results.csv."""

    with RESULTS_PATH.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writerow(row)


def run_experiment() -> None:
    """Run all three conditions three times."""

    tasks = load_tasks()
    LOGS_DIR.mkdir(exist_ok=True)

    # Start a new experiment table with the required exact header.
    with RESULTS_PATH.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()

    run_number = 1

    for condition in CONDITIONS:
        for repeat in range(1, REPEATS + 1):
            lines: list[str] = []

            def log(message: str) -> None:
                print(message)
                lines.append(message)

            log_path = LOGS_DIR / (
                f"run-{run_number:02d}-{condition}.txt"
            )

            log(
                f"provider={PROVIDER} "
                f"model={MODEL} "
                f"temperature={TEMPERATURE}"
            )
            log(
                f"run={run_number} "
                f"condition={condition} "
                f"repeat={repeat}"
            )

            try:
                result = run_round(
                    tasks=tasks,
                    condition=condition,
                    log=log,
                )

                row = {
                    "run": run_number,
                    "condition": condition,
                    "tasks": result.tasks,
                    "correct": result.correct,
                    "messages": result.messages,
                    "unassigned": result.unassigned,
                    "misawards": result.misawards,
                    "note": f"parse_fails={result.parse_fails}",
                }

                log("")
                log(
                    "[summary] "
                    f"tasks={result.tasks} "
                    f"correct={result.correct} "
                    f"messages={result.messages} "
                    f"unassigned={result.unassigned} "
                    f"misawards={result.misawards} "
                    f"parse_fails={result.parse_fails}"
                )

            except Exception as error:
                error_note = (
                    f"{type(error).__name__}: {error}"
                )

                row = {
                    "run": run_number,
                    "condition": condition,
                    "tasks": "",
                    "correct": "",
                    "messages": "",
                    "unassigned": "",
                    "misawards": "",
                    "note": error_note,
                }

                log("")
                log(f"[crash] {error_note}")
                log(traceback.format_exc())

            log_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
            )

            append_result(row)

            print(f"[saved] {log_path.name}")
            print()

            run_number += 1

    print(f"results saved: {RESULTS_PATH}")
    print(f"logs saved: {LOGS_DIR}")


if __name__ == "__main__":
    run_experiment()