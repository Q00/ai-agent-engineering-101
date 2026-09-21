import csv
import json
import os
from pathlib import Path

from manager import build_team, run_round
from model import MODEL, Meter


RESULTS = Path("results.csv")
LOG_DIR = Path("logs")
CONDITIONS = ["baseline", "homogeneous", "overconfident"]

HEADER = [
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
    with open("tasks.json", encoding="utf-8") as f:
        return json.load(f)


def next_run_number():
    if not RESULTS.exists():
        return 1

    with RESULTS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    return len(rows) + 1


def append_result(run, condition, result, meter, note=""):
    new_file = not RESULTS.exists()

    with RESULTS.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if new_file:
            writer.writerow(HEADER)

        details = (
            f"parse_fails={result.parse_fails}; "
            f"tokens={meter.tokens}"
        )
        if note:
            details += f"; {note}"

        writer.writerow([
            run,
            condition,
            result.tasks,
            result.correct,
            result.messages,
            result.unassigned,
            result.misawards,
            details,
        ])


def run_once(run, condition, tasks):
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{condition}-{run:02d}.txt"

    meter = Meter()
    lines = []

    def log(message):
        print(message)
        lines.append(str(message))

    log(
        f"provider=OpenRouter model={MODEL} temperature=0 "
        f"condition={condition}"
    )

    try:
        team = build_team(condition)
        result = run_round(tasks, team, meter, log=log)

        log(
            f"[summary] tasks={result.tasks} correct={result.correct} "
            f"messages={result.messages} unassigned={result.unassigned} "
            f"misawards={result.misawards} "
            f"parse_fails={result.parse_fails} tokens={meter.tokens}"
        )

        append_result(run, condition, result, meter)

    except Exception as exc:
        log(f"[crash] {type(exc).__name__}: {exc}")

        # Keep crashed runs as experimental evidence.
        from manager import RoundResult
        result = RoundResult(tasks=len(tasks))
        append_result(
            run,
            condition,
            result,
            meter,
            note=f"crash={type(exc).__name__}: {exc}",
        )

    finally:
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    tasks = load_tasks()
    run = next_run_number()

    for condition in CONDITIONS:
        for _ in range(3):
            print(f"\n=== run {run}: {condition} ===")
            run_once(run, condition, tasks)
            run += 1


if __name__ == "__main__":
    main()
