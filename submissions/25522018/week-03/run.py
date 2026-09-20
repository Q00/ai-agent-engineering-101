import csv
import json
from pathlib import Path

from contractor import MODEL, TEMPERATURE
from manager import run_round


ROOT = Path(__file__).resolve().parent
TASKS_FILE = ROOT / "tasks.json"
RESULTS_FILE = ROOT / "results.csv"
LOG_DIR = ROOT / "logs"

PROVIDER = "OpenRouter"

CONDITIONS = [
    "baseline",
    "homogeneous",
    "overconfident",
]

RUNS_PER_CONDITION = 3

RESULT_COLUMNS = [
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
]


def load_tasks():
    with TASKS_FILE.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def ensure_results_file():
    """
    Create results.csv and its header only if it does not
    already exist. Existing experiment results are preserved.
    """
    if RESULTS_FILE.exists() and RESULTS_FILE.stat().st_size > 0:
        return

    with RESULTS_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=RESULT_COLUMNS,
        )
        writer.writeheader()


def append_result(row):
    with RESULTS_FILE.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=RESULT_COLUMNS,
        )
        writer.writerow(row)


def run_experiment():
    tasks = load_tasks()

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    ensure_results_file()

    print(f"Provider: {PROVIDER}")
    print(f"Model: {MODEL}")
    print(f"Temperature: {TEMPERATURE}")
    print(f"Tasks: {len(tasks)}")
    print()

    experiment_run = 0

    for condition in CONDITIONS:
        for repetition in range(1, RUNS_PER_CONDITION + 1):
            experiment_run += 1

            log_file = (
                LOG_DIR
                / f"{condition}_run{repetition}.log"
            )

            log_lines = []

            # Required metadata on the first line of every log.
            metadata = (
                f"provider={PROVIDER} "
                f"model={MODEL} "
                f"temperature={TEMPERATURE}"
            )

            log_lines.append(metadata)

            def logger(message):
                text = str(message)
                print(text)
                log_lines.append(text)

            print()
            print("#" * 70)
            print(
                f"EXPERIMENT {experiment_run}/9 | "
                f"{condition} | run {repetition}"
            )
            print("#" * 70)

            try:
                result = run_round(
                    tasks=tasks,
                    condition=condition,
                    log=logger,
                )

                note = (
                    f"parse_fails={result.parse_fails}; "
                    f"provider={PROVIDER}; "
                    f"model={MODEL}; "
                    f"temperature={TEMPERATURE}"
                )

                row = {
                    "run": experiment_run,
                    "condition": condition,
                    "tasks": result.tasks,
                    "correct": result.correct,
                    "messages": result.messages,
                    "unassigned": result.unassigned,
                    "misawards": result.misawards,
                    "note": note,
                }

            except Exception as exc:
                # Do not hide or delete failed runs.
                error_text = (
                    f"{type(exc).__name__}: {exc}"
                )

                logger("")
                logger(
                    f"[CRASH] {error_text}"
                )

                row = {
                    "run": experiment_run,
                    "condition": condition,
                    "tasks": len(tasks),
                    "correct": 0,
                    "messages": 0,
                    "unassigned": 0,
                    "misawards": 0,
                    "note": (
                        f"crash={error_text}; "
                        f"parse_fails=NA"
                    ),
                }

            # Save the complete console-style log.
            log_file.write_text(
                "\n".join(log_lines) + "\n",
                encoding="utf-8",
            )

            # Save one row even when a run crashes.
            append_result(row)

            print(
                f"[saved] {log_file.name}"
            )

    print()
    print("=" * 70)
    print("EXPERIMENT FINISHED")
    print(f"Results: {RESULTS_FILE}")
    print(f"Logs:    {LOG_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    run_experiment()