"""Run three Contract Net conditions and preserve every run as evidence.

Usage (from this directory): python run_experiment.py --runs 3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path

from contract_net import (
    CONDITIONS,
    MAX_TOKENS,
    MODEL,
    PROVIDER,
    TEMPERATURE,
    Meter,
    OpenRouterChat,
    make_team,
    run_contract_net,
)


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
KEY_PATTERN = re.compile(r"(?:sk-ant-|sk-or-v1-|sk-proj-)[A-Za-z0-9_-]+")


def safe_text(value: object) -> str:
    """Keep exceptions useful without ever preserving a recognizable API key."""
    return KEY_PATTERN.sub("[REDACTED_API_KEY]", str(value))


def load_tasks(path: Path) -> list[dict]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or len(tasks) != 6:
        raise ValueError("tasks.json must contain exactly six tasks")
    expected_golds = ["A", "A", "B", "B", "C", "C"]
    for index, (task, gold) in enumerate(zip(tasks, expected_golds), start=1):
        if not isinstance(task, dict) or set(task) != {"id", "desc", "gold"}:
            raise ValueError(f"task {index} must contain exactly id, desc, gold")
        if task["id"] != index or not isinstance(task["desc"], str):
            raise ValueError(f"task {index} has an invalid id or description")
        if task["gold"] != gold:
            raise ValueError(f"task {index} gold must be {gold}")
    return tasks


def refuse_to_overwrite(base: Path) -> None:
    results = base / "results.csv"
    log_dir = base / "logs"
    if results.exists():
        raise SystemExit("results.csv already exists; refusing to overwrite experiment evidence")
    if log_dir.exists() and any(log_dir.iterdir()):
        raise SystemExit("logs/ is not empty; refusing to overwrite experiment evidence")


def write_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs != 3:
        raise SystemExit("this experiment is fixed at exactly --runs 3")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; no experiment was started")

    base = Path(__file__).resolve().parent
    refuse_to_overwrite(base)
    tasks = load_tasks(base / "tasks.json")
    log_dir = base / "logs"
    log_dir.mkdir(exist_ok=True)

    results_path = base / "results.csv"
    with results_path.open("x", encoding="utf-8", newline="") as result_file:
        writer = csv.writer(result_file)
        writer.writerow(HEADER)
        result_file.flush()
        global_run = 0

        for condition in CONDITIONS:
            for condition_run in range(1, args.runs + 1):
                global_run += 1
                lines: list[str] = []

                def log(message: str) -> None:
                    clean = safe_text(message)
                    print(clean)
                    lines.extend(clean.splitlines() or [""])

                log(f"[setup] provider={PROVIDER}")
                log(f"[setup] model={MODEL}")
                log(f"[setup] temperature={TEMPERATURE:g}")
                log(f"[setup] max_tokens={MAX_TOKENS}")
                log(f"[setup] condition={condition}")
                log(f"[setup] run={global_run}")
                log("[setup] contractor_order=A,B,C")
                log("[setup] award_rule=highest confidence; ties use response order")
                for contractor in make_team(condition):
                    log(
                        f"[setup] contractor={contractor.name} "
                        f"system_prompt={contractor.system_prompt}"
                    )

                meter = Meter()
                caller = OpenRouterChat(meter)
                log_path = log_dir / f"{condition}-{condition_run:02d}.txt"
                try:
                    metrics = run_contract_net(tasks, condition, caller, log)
                    note = (
                        f"parse_fails={metrics.parse_fails}; "
                        f"C_awards={metrics.c_awards}; "
                        f"tokens={meter.tokens}; calls={meter.calls}"
                    )
                    log(
                        f"[summary] tasks={metrics.tasks} correct={metrics.correct} "
                        f"messages={metrics.messages} unassigned={metrics.unassigned} "
                        f"misawards={metrics.misawards} "
                        f"parse_fails={metrics.parse_fails} C_awards={metrics.c_awards}"
                    )
                    row = [
                        global_run,
                        condition,
                        metrics.tasks,
                        metrics.correct,
                        metrics.messages,
                        metrics.unassigned,
                        metrics.misawards,
                        note,
                    ]
                except (Exception, KeyboardInterrupt) as exc:
                    note = f"crash: {type(exc).__name__}: {safe_text(exc)}"
                    log(f"[crash] {note}")
                    row = [global_run, condition, "", "", "", "", "", note]
                    writer.writerow(row)
                    result_file.flush()
                    write_log(log_path, lines)
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    continue

                writer.writerow(row)
                result_file.flush()
                write_log(log_path, lines)


if __name__ == "__main__":
    main()

