"""Run three repeats of the free, tagged, and structured negotiations.

Examples:
    python run_experiment.py
    python run_experiment.py --condition structured --repeat 1

Rows are appended after every episode. Existing (run, scenario) pairs are
skipped, so the same command safely resumes an interrupted experiment.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from pathlib import Path

import llm
from negotiate import EpisodeResult, run_episode


BASE = Path(__file__).resolve().parent
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
CONDITIONS = ("free", "tagged", "structured")
REPEATS = (1, 2, 3)


def _force_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, io.UnsupportedOperation):
        pass


def run_number(condition: str, repeat: int) -> int:
    return CONDITIONS.index(condition) * len(REPEATS) + repeat


def _prepare_results(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(HEADER)
        return set()

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        if handle.seekable():
            handle.seek(0)
            header = next(csv.reader(handle), [])
    if header != HEADER:
        raise ValueError(f"unexpected results header: {header}")
    return {(row["run"], row["scenario"]) for row in rows}


def _result_row(run: int, condition: str, result: EpisodeResult, note: str):
    return [
        run,
        condition,
        result.scenario,
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


class RunLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = str(message)
        print(line, flush=True)
        self.handle.write(line + "\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def _run_one(
    run: int,
    repeat: int,
    condition: str,
    scenarios: list[dict[str, object]],
    writer,
    results_handle,
    completed: set[tuple[str, str]],
    logs_dir: Path,
) -> None:
    log = RunLog(logs_dir / f"{condition}-{repeat:02d}.txt")
    try:
        log("")
        log(
            f"[run] {run:02d} condition={condition} repeat={repeat} "
            f"provider={llm.PROVIDER} model={llm.MODEL} "
            f"temperature={llm.TEMPERATURE} max_turns=8"
        )
        for scenario in scenarios:
            key = (str(run), str(scenario["id"]))
            if key in completed:
                log(f"[skip] scenario={scenario['id']} already recorded")
                continue

            log(
                f"[episode] scenario={scenario['id']} item={scenario['item']} "
                f"reserve={scenario['reserve']} budget={scenario['budget']}"
            )
            meter = llm.Meter()
            started = time.time()

            def agent_call(system, history):
                return llm.call_model(system, history, meter, log=log)

            def reader_call(system, user):
                return llm.call_model(
                    system,
                    [{"role": "user", "content": user}],
                    meter,
                    log=log,
                )

            try:
                result = run_episode(
                    scenario,
                    condition,
                    agent_call,
                    reader_call,
                    log=log,
                )
                combined_note = "; ".join(
                    part for part in (
                        result.note,
                        f"model={llm.MODEL} calls={meter.calls} "
                        f"retries={meter.retries} tokens={meter.tokens}",
                    ) if part
                )
                row = _result_row(run, condition, result, combined_note)
            except Exception as exc:
                note = f"crash: {type(exc).__name__}: {str(exc)[:400]}"
                log(f"[crash] {note}")
                row = [
                    run,
                    condition,
                    scenario["id"],
                    int(scenario["reserve"] <= scenario["budget"]),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    note,
                ]

            writer.writerow(row)
            results_handle.flush()
            completed.add(key)
            log(f"[elapsed] scenario={scenario['id']} seconds={time.time() - started:.1f}")
    finally:
        log.close()


def main() -> None:
    _force_utf8_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    args = parser.parse_args()

    scenarios_path = BASE / "scenarios.json"
    results_path = BASE / "results.csv"
    logs_dir = BASE / "logs"
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))
    completed = _prepare_results(results_path)

    selected_conditions = (args.condition,) if args.condition else CONDITIONS
    selected_repeats = (args.repeat,) if args.repeat else REPEATS
    with results_path.open("a", newline="", encoding="utf-8") as results_handle:
        writer = csv.writer(results_handle)
        for condition in selected_conditions:
            for repeat in selected_repeats:
                _run_one(
                    run_number(condition, repeat),
                    repeat,
                    condition,
                    scenarios,
                    writer,
                    results_handle,
                    completed,
                    logs_dir,
                )


if __name__ == "__main__":
    main()
