import argparse
import csv
import json
from pathlib import Path

from negotiation import run_episode, TURN_LIMIT
from tools_shared import (
    MODEL, PROVIDER, TEMPERATURE, MAX_COMPLETION_TOKENS,
)


FIELDS = [
    "run", "condition", "scenario", "deal_possible",
    "outcome", "price", "correct", "violation", "turns",
    "format_errors", "reader_calls", "note",
]

RUN_START = {"free": 1, "tagged": 4, "structured": 7}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("condition", choices=RUN_START)
    parser.add_argument("--repeat", type=int, choices=[1, 2, 3], required=True)
    args = parser.parse_args()

    folder = Path(__file__).resolve().parent
    scenarios = json.loads(
        (folder / "scenarios.json").read_text(encoding="utf-8")
    )

    run = RUN_START[args.condition] + args.repeat - 1
    csv_path = folder / "results.csv"
    log_folder = folder / "logs"
    log_folder.mkdir(exist_ok=True)
    log_path = log_folder / f"run_{run}_{args.condition}.txt"

    completed = set()
    if csv_path.exists() and csv_path.stat().st_size:
        with csv_path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != FIELDS:
                raise ValueError("Existing results.csv has the wrong header.")
            for row in reader:
                completed.add((int(row["run"]), int(row["scenario"])))

    pending = [
        scenario for scenario in scenarios
        if (run, scenario["id"]) not in completed
    ]

    if not pending:
        print(f"Run {run} already recorded. No API calls made.")
        return

    header = (
        f"provider={PROVIDER}, model={MODEL}, "
        f"temperature={TEMPERATURE}, turn_limit={TURN_LIMIT}, "
        f"max_completion_tokens={MAX_COMPLETION_TOKENS}, "
        f"condition={args.condition}, run={run}"
    )

    if log_path.exists() and log_path.stat().st_size:
        with log_path.open(encoding="utf-8") as file:
            if file.readline().rstrip("\n") != header:
                raise ValueError("Settings differ from this run's existing log.")

    new_csv = not csv_path.exists() or csv_path.stat().st_size == 0
    new_log = not log_path.exists() or log_path.stat().st_size == 0

    with (
        csv_path.open("a", newline="", encoding="utf-8") as csv_file,
        log_path.open("a", encoding="utf-8") as log_file,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        if new_csv:
            writer.writeheader()
            csv_file.flush()

        def log(text):
            print(text, flush=True)
            log_file.write(text + "\n")
            log_file.flush()

        if new_log:
            log(header)
        else:
            log("[resume] Continuing this run; recorded episodes are skipped.")

        for scenario in pending:
            log(f"[episode start] run={run}, scenario={scenario['id']}")
            interrupted = False

            try:
                result = run_episode(scenario, args.condition, log)
                row = {
                    "run": run,
                    "condition": args.condition,
                    **result,
                }
            except (Exception, KeyboardInterrupt) as error:
                interrupted = isinstance(error, KeyboardInterrupt)
                note = (
                    f"{type(error).__name__}; "
                    f"HTTP status={getattr(error, 'status_code', 'n/a')}"
                )
                row = {field: "" for field in FIELDS}
                row.update(
                    run=run,
                    condition=args.condition,
                    scenario=scenario["id"],
                    note=note,
                )
                log(f"[crashed episode] {note}")

            writer.writerow(row)
            csv_file.flush()
            log(f"[saved row] {json.dumps(row)}")

            if interrupted:
                print("Interrupted episode saved. Stopping.")
                break

    print(f"Results: {csv_path}")
    print(f"Log: {log_path}")


if __name__ == "__main__":
    main()