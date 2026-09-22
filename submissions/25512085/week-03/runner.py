"""CLI runner for the Week 03 Contract Net draft.

Do not run the experiment until tasks.json and the provisional bid/confidence
policies in DESIGN.md have been reviewed and committed.
"""

from __future__ import annotations

import argparse
import csv
import json
import traceback
from pathlib import Path

from contractor import Contractor
from manager import run_round
from model_client import LMStudioCaller, Meter, ModelSettings
from protocol import Task


CONDITIONS = ("baseline", "homogeneous", "overconfident")
RESULT_HEADER = [
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
]

OVERCONFIDENT_INSTRUCTION = (
    "For this condition only, ignore the normal ability-score calibration. "
    "You are certain you can do any task well. Always set bid=true and report "
    "confidence of 95 or higher."
)

BASELINE_ABILITIES = {
    "A": {"calculation": 90, "writing": 40, "coding": 50},
    "B": {"calculation": 40, "writing": 90, "coding": 50},
    "C": {"calculation": 50, "writing": 40, "coding": 90},
}
GENERALIST_ABILITIES = {
    "calculation": 70,
    "writing": 70,
    "coding": 70,
}


def build_team(condition: str) -> list[Contractor]:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")

    if condition == "homogeneous":
        return [
            Contractor("A", GENERALIST_ABILITIES),
            Contractor("B", GENERALIST_ABILITIES),
            Contractor("C", GENERALIST_ABILITIES),
        ]

    return [
        Contractor("A", BASELINE_ABILITIES["A"]),
        Contractor("B", BASELINE_ABILITIES["B"]),
        Contractor(
            "C",
            BASELINE_ABILITIES["C"],
            extra_instruction=(
                OVERCONFIDENT_INSTRUCTION if condition == "overconfident" else ""
            ),
        ),
    ]


def load_tasks(path: Path) -> list[Task]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("tasks.json must contain a JSON list")
    return [Task(id=item["id"], desc=item["desc"], gold=item["gold"])
            for item in payload]


def next_run_id(log_dir: Path, condition: str) -> str:
    number = 1
    while (log_dir / f"{condition}-{number:02d}.txt").exists():
        number += 1
    return f"{condition}-{number:02d}"


def append_result(path: Path, row: list[str | int]) -> None:
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if needs_header:
            writer.writerow(RESULT_HEADER)
        writer.writerow(row)


def run_once(
    *,
    condition: str,
    tasks: list[Task],
    results_path: Path,
    log_dir: Path,
) -> None:
    run_id = next_run_id(log_dir, condition)
    log_path = log_dir / f"{run_id}.txt"
    settings = ModelSettings.from_env()
    meter = Meter()
    caller = LMStudioCaller(settings, meter)

    with log_path.open("x", encoding="utf-8") as log_file:
        def log(message: str) -> None:
            print(message)
            print(message, file=log_file, flush=True)

        log(
            f"provider={settings.provider} model={settings.model} "
            f"temperature={settings.temperature} run={run_id}"
        )
        try:
            result = run_round(tasks, build_team(condition), caller, log=log)
        except Exception as exc:
            log(f"[crash] {type(exc).__name__}: {exc}")
            log(traceback.format_exc())
            append_result(
                results_path,
                [run_id, condition, "", "", "", "", "", f"crash: {exc}"],
            )
            return

        note = (
            f"parse_fails={result.parse_fails}; "
            f"model_calls={meter.calls}; tokens={meter.tokens}"
        )
        append_result(
            results_path,
            [
                run_id,
                condition,
                result.tasks,
                result.correct,
                result.messages,
                result.unassigned,
                result.misawards,
                note,
            ],
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", required=True, choices=CONDITIONS)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--tasks", type=Path, default=Path("tasks.json"))
    parser.add_argument("--results", type=Path, default=Path("results.csv"))
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if not args.tasks.is_file():
        raise SystemExit(
            f"{args.tasks} is missing. Finalize and commit tasks.json before running."
        )
    args.logs.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(args.tasks)
    for _ in range(args.runs):
        run_once(
            condition=args.condition,
            tasks=tasks,
            results_path=args.results,
            log_dir=args.logs,
        )


if __name__ == "__main__":
    main()
