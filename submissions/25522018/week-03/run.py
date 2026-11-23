import csv
import json
import sys
from pathlib import Path

from contractor import MODEL, TEMPERATURE
from manager import run_round


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent
TASKS_FILE = ROOT / "tasks.json"
RESULTS_FILE = ROOT / "results.csv"
LOG_DIR = ROOT / "logs"

PROVIDER = "OpenRouter"

VALID_CONDITIONS = [
    "baseline",
    "homogeneous",
    "overconfident",
]

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
    with TASKS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_results_file():
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


def get_next_run_number():
    if not RESULTS_FILE.exists():
        return 1

    numbers = []

    with RESULTS_FILE.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
                numbers.append(int(row["run"]))
            except (ValueError, KeyError):
                pass

    return max(numbers, default=0) + 1


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


def run_one(condition, repetition):
    if condition not in VALID_CONDITIONS:
        raise ValueError(
            "Condition must be baseline, homogeneous, or overconfident."
        )

    tasks = load_tasks()

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    ensure_results_file()

    experiment_run = get_next_run_number()

    log_file = (
        LOG_DIR
        / f"run{experiment_run:02d}_{condition}_rep{repetition}.log"
    )

    log_lines = []

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

    print("=" * 70)
    print(
        f"RUN {experiment_run} | "
        f"{condition} | repetition {repetition}"
    )
    print("=" * 70)

    try:
        result = run_round(
            tasks=tasks,
            condition=condition,
            log=logger,
        )

        row = {
            "run": experiment_run,
            "condition": condition,
            "tasks": result.tasks,
            "correct": result.correct,
            "messages": result.messages,
            "unassigned": result.unassigned,
            "misawards": result.misawards,
            "note": (
                f"repetition={repetition}; "
                f"parse_fails={result.parse_fails}; "
                f"provider={PROVIDER}; "
                f"model={MODEL}; "
                f"temperature={TEMPERATURE}"
            ),
        }

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"

        logger("")
        logger(f"[CRASH] {error_text}")

        row = {
            "run": experiment_run,
            "condition": condition,
            "tasks": len(tasks),
            "correct": 0,
            "messages": 0,
            "unassigned": 0,
            "misawards": 0,
            "note": (
                f"repetition={repetition}; "
                f"crash={error_text}; "
                f"parse_fails=NA"
            ),
        }

    log_file.write_text(
        "\n".join(log_lines) + "\n",
        encoding="utf-8",
    )

    append_result(row)

    print()
    print(f"[saved] {log_file}")
    print(f"[results] {RESULTS_FILE}")


def main():
    if len(sys.argv) != 3:
        print(
            "Usage:\n"
            "  py run.py baseline 1\n"
            "  py run.py baseline 2\n"
            "  py run.py homogeneous 1\n"
            "  py run.py overconfident 1"
        )
        sys.exit(1)

    condition = sys.argv[1]

    try:
        repetition = int(sys.argv[2])
    except ValueError:
        print("Repetition must be a number such as 1, 2, or 3.")
        sys.exit(1)

    run_one(
        condition=condition,
        repetition=repetition,
    )


if __name__ == "__main__":
    main()