"""Repeat and record the Week 04 negotiation experiment without overwriting evidence."""

from __future__ import annotations

import argparse
import csv
import json
import time
import traceback
from pathlib import Path
from typing import Callable, Iterable

from acl import MAX_TURNS
from model_client import LMStudioCaller, Meter, ModelSettings
from negotiate import EpisodeResult, run_episode


CONDITIONS = ("free", "tagged", "structured")
RESULT_HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]
MAX_429_RETRIES = 5


def load_scenarios(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("scenarios.json must be a non-empty JSON list")
    return payload


def completed_pairs(path: Path) -> set[tuple[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        if rows.fieldnames != RESULT_HEADER:
            raise ValueError("existing results.csv has an unexpected header")
        return {
            (row["run"].strip(), row["scenario"].strip())
            for row in rows
            if row.get("run", "").strip() and row.get("scenario", "").strip()
        }


def append_row(path: Path, row: list[object]) -> None:
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(RESULT_HEADER)
        writer.writerow(row)


def result_row(run_id: str, condition: str, result: EpisodeResult, note: str) -> list[object]:
    return [
        run_id,
        condition,
        result.scenario_id,
        result.deal_possible,
        result.outcome,
        "" if result.price is None else result.price,
        result.correct,
        result.violation,
        result.turns,
        result.format_errors,
        result.reader_calls,
        note,
    ]


def retrying_caller(base: Callable[[str, str], str], log: Callable[[str], None]) -> Callable[[str, str], str]:
    """Retry only burst-rate-limit failures, retaining the final exception."""

    def call(system: str, user: str) -> str:
        for attempt in range(MAX_429_RETRIES + 1):
            try:
                return base(system, user)
            except RuntimeError as exc:
                if "HTTP 429" not in str(exc) or attempt == MAX_429_RETRIES:
                    raise
                wait_seconds = 2**attempt
                log(f"[retry] HTTP 429; waiting {wait_seconds}s before retry {attempt + 1}/{MAX_429_RETRIES}")
                time.sleep(wait_seconds)
        raise AssertionError("unreachable")

    return call


def run_one(
    run_id: str,
    condition: str,
    scenarios: Iterable[dict[str, object]],
    completed: set[tuple[str, str]],
    settings: ModelSettings,
    results_path: Path,
    log_dir: Path,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.txt"
    is_resume = log_path.exists()
    meter = Meter()

    with log_path.open("a" if is_resume else "x", encoding="utf-8") as log_file:
        def log(message: str) -> None:
            print(message)
            print(message, file=log_file, flush=True)

        if is_resume:
            log(f"[resume] run={run_id}")
        else:
            log(
                f"provider=LM Studio native API server={settings.server_url} "
                f"model={settings.model} temperature={settings.temperature} "
                f"max_turns={MAX_TURNS} condition={condition} run={run_id}"
            )

        caller = retrying_caller(LMStudioCaller(settings, meter), log)
        for scenario in scenarios:
            scenario_id = str(scenario["id"])
            if (run_id, scenario_id) in completed:
                log(f"[skip] scenario={scenario_id}; an existing results.csv row is retained")
                continue

            calls_before = meter.calls
            log(f"[start] scenario={scenario_id}")
            try:
                result = run_episode(scenario, condition, caller, log)
            except Exception as exc:
                log(f"[crash] scenario={scenario_id} {type(exc).__name__}: {exc}")
                log(traceback.format_exc())
                append_row(
                    results_path,
                    [run_id, condition, scenario_id, "", "", "", "", "", "", "", "", f"crash: {type(exc).__name__}: {exc}"],
                )
            else:
                all_calls = meter.calls - calls_before
                append_row(
                    results_path,
                    result_row(run_id, condition, result, f"all_model_calls={all_calls}"),
                )
            completed.add((run_id, scenario_id))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=CONDITIONS, help="run one condition; default is all three")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--run-prefix", default="", help="prefix new run IDs to preserve earlier experiments")
    parser.add_argument("--retry-scenario", help="run only this scenario as a recorded supplemental episode")
    parser.add_argument("--retry-run", help="new run ID for the supplemental episode")
    parser.add_argument("--scenarios", type=Path, default=Path("scenarios.json"))
    parser.add_argument("--results", type=Path, default=Path("results.csv"))
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    parser.add_argument("--dry-run", action="store_true", help="list pending episodes without model calls or file writes")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in args.run_prefix):
        raise SystemExit("--run-prefix may contain only letters, digits, hyphens and underscores")
    if bool(args.retry_scenario) != bool(args.retry_run):
        raise SystemExit("--retry-scenario and --retry-run must be supplied together")
    if args.retry_scenario and not args.condition:
        raise SystemExit("--condition is required for a supplemental episode")
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    scenarios = load_scenarios(args.scenarios)
    completed = completed_pairs(args.results)
    if args.retry_scenario:
        selected = [s for s in scenarios if str(s["id"]) == args.retry_scenario]
        if len(selected) != 1:
            raise SystemExit(f"scenario ID must match exactly one scenario: {args.retry_scenario}")
        existing_runs = {run_id for run_id, _ in completed}
        if args.retry_run in existing_runs and (args.retry_run, args.retry_scenario) not in completed:
            raise SystemExit("--retry-run already belongs to a different recorded episode")
        pending = (args.retry_run, args.retry_scenario) not in completed
        if args.dry_run:
            print(f"pending_episodes={int(pending)}")
            if pending:
                print(f"{args.retry_run},{args.condition},{args.retry_scenario}")
            return
        if pending:
            settings = ModelSettings.from_env()
            run_one(args.retry_run, args.condition, selected, completed, settings, args.results, args.logs)
        return
    conditions = (args.condition,) if args.condition else CONDITIONS
    planned = [
        (f"{args.run_prefix}{condition}-{repeat:02d}", condition, str(scenario["id"]))
        for condition in conditions
        for repeat in range(1, args.repeats + 1)
        for scenario in scenarios
        if (f"{args.run_prefix}{condition}-{repeat:02d}", str(scenario["id"])) not in completed
    ]
    if args.dry_run:
        print(f"pending_episodes={len(planned)}")
        for run_id, condition, scenario_id in planned:
            print(f"{run_id},{condition},{scenario_id}")
        return

    settings = ModelSettings.from_env()
    for condition in conditions:
        for repeat in range(1, args.repeats + 1):
            run_id = f"{args.run_prefix}{condition}-{repeat:02d}"
            run_one(run_id, condition, scenarios, completed, settings, args.results, args.logs)


if __name__ == "__main__":
    main()
