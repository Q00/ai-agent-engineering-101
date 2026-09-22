"""Run three Contract Net conditions and preserve every run as evidence.

Usage (from this directory): python run_experiment.py --runs 3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from contract_net import (
    CONDITIONS,
    BASE_URL,
    MAX_TOKENS,
    MODEL,
    PROVIDER,
    REASONING_ENABLED,
    TEMPERATURE,
    Meter,
    OpenRouterChat,
    make_team,
    protocol_fingerprint,
    run_contract_net,
    validate_runtime_config,
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
def safe_text(value: object) -> str:
    """Redact the configured secret without embedding key prefixes in source."""
    text = str(value)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    return text.replace(api_key, "[REDACTED]") if api_key else text


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


def last_recorded_run(results_path: Path) -> int:
    """Validate existing evidence and return its largest global run number."""
    if not results_path.exists():
        return 0

    with results_path.open(encoding="utf-8", newline="") as result_file:
        rows = list(csv.reader(result_file))
    if not rows or rows[0] != HEADER:
        raise ValueError("existing results.csv has an unexpected header")

    seen: set[int] = set()
    for line_no, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(HEADER):
            raise ValueError(f"existing results.csv line {line_no} has the wrong column count")
        try:
            run = int(row[0])
        except ValueError as exc:
            raise ValueError(f"existing results.csv line {line_no} has an invalid run") from exc
        if run < 1 or run in seen:
            raise ValueError(f"existing results.csv line {line_no} has a duplicate/invalid run")
        if row[1] not in CONDITIONS:
            raise ValueError(f"existing results.csv line {line_no} has an invalid condition")
        for value in row[2:7]:
            if value.strip() and not value.strip().isdigit():
                raise ValueError(f"existing results.csv line {line_no} has an invalid count")
        seen.add(run)
    return max(seen, default=0)


def next_log_path(log_dir: Path, condition: str) -> Path:
    """Choose a new per-condition filename without reusing existing evidence."""
    index = 1
    while True:
        candidate = log_dir / f"{condition}-{index:02d}.txt"
        if not candidate.exists():
            return candidate
        index += 1


def write_log(path: Path, lines: list[str]) -> None:
    with path.open("x", encoding="utf-8") as log_file:
        log_file.write("\n".join(lines) + "\n")


def is_rate_limit_error(exc: BaseException) -> bool:
    """Recognize an HTTP 429 without importing an SDK-specific exception."""
    if getattr(exc, "status_code", None) == 429:
        return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 429


def api_error_metadata(exc: BaseException) -> dict:
    """Return non-secret diagnostic fields from an SDK/API exception."""
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None:
        status = getattr(response, "status_code", None)
    body = getattr(exc, "body", None)
    code = None
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            code = error.get("code")
    return {
        "error_type": type(exc).__name__,
        "status": status,
        "code": code,
    }


def expected_api_calls(task_count: int, condition_count: int, runs: int) -> int:
    return task_count * 3 * condition_count * runs


def smoke_ready_path(base: Path, tasks: list[dict]) -> Path:
    fingerprint = protocol_fingerprint(tasks)
    return base / "smoke_logs" / f"smoke-ready-{fingerprint}.json"


def smoke_gate_is_open(path: Path, fingerprint: str) -> bool:
    """Accept only a complete three-call diagnostic with no API failures."""
    if not path.is_file():
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        record.get("protocol_fingerprint") == fingerprint
        and record.get("calls_completed") == 3
        and record.get("api_errors") == 0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--condition", choices=("all", *CONDITIONS), default="all")
    args = parser.parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; no experiment was started")
    validate_runtime_config()

    base = Path(__file__).resolve().parent
    tasks = load_tasks(base / "tasks.json")
    fingerprint = protocol_fingerprint(tasks)
    ready_path = smoke_ready_path(base, tasks)
    if not smoke_gate_is_open(ready_path, fingerprint):
        raise SystemExit(
            "matching three-call smoke test has not completed without API errors; "
            "run smoke_test.py before the experiment"
        )
    log_dir = base / "logs"
    log_dir.mkdir(exist_ok=True)

    results_path = base / "results.csv"
    global_run = last_recorded_run(results_path)
    new_results = not results_path.exists()
    mode = "x" if new_results else "a"
    with results_path.open(mode, encoding="utf-8", newline="") as result_file:
        writer = csv.writer(result_file)
        if new_results:
            writer.writerow(HEADER)
            result_file.flush()

        selected_conditions = CONDITIONS if args.condition == "all" else (args.condition,)
        for condition in selected_conditions:
            for _ in range(args.runs):
                global_run += 1
                lines: list[str] = []

                def log(message: str) -> None:
                    clean = safe_text(message)
                    print(clean)
                    lines.extend(clean.splitlines() or [""])

                log(f"[setup] provider={PROVIDER}")
                log(f"[setup] model={MODEL}")
                log(f"[setup] base_url={BASE_URL}")
                log(f"[setup] temperature={TEMPERATURE:g}")
                log(f"[setup] max_tokens={MAX_TOKENS}")
                log(f"[setup] reasoning_enabled={str(REASONING_ENABLED).lower()}")
                log(f"[setup] protocol_fingerprint={fingerprint}")
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
                log_path = next_log_path(log_dir, condition)
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
                    if is_rate_limit_error(exc):
                        print("[stop] rate limit reached; no further runs were attempted")
                        return
                    continue

                writer.writerow(row)
                result_file.flush()
                write_log(log_path, lines)


if __name__ == "__main__":
    main()
