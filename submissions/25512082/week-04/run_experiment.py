"""Run or resume the Week 04 negotiation experiment.

No files or model calls occur on import. Execute explicitly with:
    python run_experiment.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from acl import CONDITIONS, READER_SYSTEM
from model_client import (
    CallMeter,
    ModelClient,
    ModelConfig,
    RateLimitError,
    redact_secrets,
)
from negotiate import MAX_MESSAGES, run_episode


HEADER = [
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "price",
    "correct",
    "violation",
    "turns",
    "format_errors",
    "reader_calls",
    "note",
]
RUNS = (1, 2, 3)


def load_scenarios(path: Path) -> list[dict]:
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(scenarios, list) or len(scenarios) < 4:
        raise ValueError("scenarios.json must contain at least four scenarios")
    ids: set[str] = set()
    possible = impossible = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not all(
            key in scenario for key in ("id", "item", "reserve", "budget")
        ):
            raise ValueError("each scenario requires id, item, reserve, and budget")
        scenario_id = str(scenario["id"])
        if scenario_id in ids:
            raise ValueError(f"duplicate scenario id: {scenario_id}")
        ids.add(scenario_id)
        if type(scenario["reserve"]) is not int or type(scenario["budget"]) is not int:
            raise ValueError(f"scenario {scenario_id} limits must be integers")
        if scenario["reserve"] <= scenario["budget"]:
            possible += 1
        else:
            impossible += 1
    if not possible or not impossible:
        raise ValueError("scenario set needs both possible and impossible deals")
    return scenarios


def load_completed(path: Path) -> set[tuple[str, int, str]]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as result_file:
        reader = csv.DictReader(result_file)
        if reader.fieldnames != HEADER:
            raise ValueError("existing results.csv has an unexpected header")
        completed: set[tuple[str, int, str]] = set()
        for line_number, row in enumerate(reader, start=2):
            try:
                key = (row["condition"], int(row["run"]), row["scenario"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"existing results.csv line {line_number} has an invalid key"
                ) from exc
            if key in completed:
                raise ValueError(f"duplicate existing episode key: {key}")
            completed.add(key)  # Includes crash rows by policy.
    return completed


def make_agent(client: ModelClient):
    def agent(role: str, system: str, history: list[dict[str, str]]) -> str:
        del role
        return client.complete(
            [{"role": "system", "content": system}, *history], purpose="agent"
        )

    return agent


def make_reader(client: ModelClient):
    def reader(transcript: list[dict[str, str]]) -> str:
        rendered = "\n".join(
            f"{message['speaker']}: {message['content']}" for message in transcript
        )
        return client.complete(
            [
                {"role": "system", "content": READER_SYSTEM},
                {"role": "user", "content": rendered},
            ],
            purpose="reader",
        )

    return reader


def append_row(writer: csv.writer, result_file, row: list[object]) -> None:
    writer.writerow(row)
    result_file.flush()


def run_all(base: Path) -> None:
    config = ModelConfig.from_env()
    scenarios = load_scenarios(base / "scenarios.json")
    results_path = base / "results.csv"
    completed = load_completed(results_path)
    new_results = not results_path.exists()
    logs_dir = base / "logs"
    logs_dir.mkdir(exist_ok=True)

    with results_path.open(
        "w" if new_results else "a", encoding="utf-8", newline=""
    ) as result_file:
        writer = csv.writer(result_file)
        if new_results:
            writer.writerow(HEADER)
            result_file.flush()

        for condition in CONDITIONS:
            for run in RUNS:
                log_path = logs_dir / f"{condition}-{run:02d}.log"
                mode = "a" if log_path.exists() else "x"
                with log_path.open(mode, encoding="utf-8") as log_file:
                    if mode == "x":
                        log_file.write(
                            f"provider={config.provider} model={config.model} "
                            f"temperature={config.temperature:g} "
                            f"max_messages={MAX_MESSAGES} condition={condition} run={run}\n"
                        )
                    else:
                        log_file.write("[resume]\n")
                    log_file.flush()

                    def log(message: str) -> None:
                        print(message)
                        log_file.write(message + "\n")
                        log_file.flush()

                    for scenario in scenarios:
                        scenario_id = str(scenario["id"])
                        key = (condition, run, scenario_id)
                        if key in completed:
                            log(f"[skip] scenario={scenario_id} already recorded")
                            continue
                        log(
                            f"[scenario] id={scenario_id} item={scenario['item']} "
                            f"reserve={scenario['reserve']} budget={scenario['budget']}"
                        )
                        meter = CallMeter()
                        client = ModelClient(config, meter)
                        try:
                            result = run_episode(
                                scenario,
                                condition,
                                make_agent(client),
                                None if condition == "structured" else make_reader(client),
                                log=log,
                            )
                        except RateLimitError as exc:
                            # No CSV row: the same key remains retryable on restart.
                            log(
                                f"[rate-limit] scenario={scenario_id} "
                                f"error={redact_secrets(exc)}"
                            )
                            return
                        except (Exception, KeyboardInterrupt) as exc:
                            note = (
                                f"crash: {type(exc).__name__}: {redact_secrets(exc)}"
                            )
                            log(f"[crash] scenario={scenario_id} {note}")
                            append_row(
                                writer,
                                result_file,
                                [run, condition, scenario_id, "", "", "", "", "", "", "", "", note],
                            )
                            completed.add(key)
                            if isinstance(exc, KeyboardInterrupt):
                                raise
                            continue

                        append_row(
                            writer,
                            result_file,
                            [
                                run,
                                condition,
                                scenario_id,
                                int(scenario["reserve"] <= scenario["budget"]),
                                result.outcome,
                                "" if result.price is None else result.price,
                                result.correct,
                                result.violation,
                                result.turns,
                                result.format_errors,
                                result.reader_calls,
                                result.note,
                            ],
                        )
                        completed.add(key)


if __name__ == "__main__":
    run_all(Path(__file__).resolve().parent)
