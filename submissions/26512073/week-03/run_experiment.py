import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from bidding import request_bid
from contractors import build_team
from contract_net import run_tasks
from tools_shared import Meter, MODEL, PROVIDER

FOLDER = Path(__file__).resolve().parent
FIELDS = [
    "run", "condition", "tasks", "correct", "messages",
    "unassigned", "misawards", "note",
]


def run_once(condition):
    tasks = json.loads(
        (FOLDER / "tasks.json").read_text(encoding="utf-8")
    )
    results_path = FOLDER / "results.csv"

    previous = []
    if results_path.exists() and results_path.stat().st_size:
        with results_path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != FIELDS:
                raise ValueError("Existing results.csv has a different header")
            previous = list(reader)

    run_id = max((int(row["run"]) for row in previous), default=0) + 1
    log_folder = FOLDER / "logs"
    log_folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_folder / f"run_{run_id}_{condition}_{stamp}.txt"

    row = dict.fromkeys(FIELDS, "")
    row.update(run=run_id, condition=condition)
    meter = Meter()
    succeeded = False

    with log_path.open("x", encoding="utf-8") as file:
        def log(message):
            print(message, flush=True)
            file.write(message + "\n")
            file.flush()

        log(f"run={run_id}, condition={condition}")
        log(
            f"provider={PROVIDER}, model={MODEL}, "
            "temperature=0, max_completion_tokens=300"
        )
        log("Tie rule: A before B before C.")
        log("Messages: announcements + true bids + awards.")
        log("Invalid replies count as no bid; failures are counted.")

        try:
            def get_bid(contractor, task):
                return request_bid(contractor, task, meter, log)

            counts = run_tasks(tasks, build_team(condition), get_bid, log)

            for field in FIELDS[2:7]:
                row[field] = counts[field]

            row["note"] = f"parse_failures={counts['parse_failures']}"
            succeeded = True

        except (Exception, KeyboardInterrupt) as error:
            # Record failure without printing credentials or response bodies.
            status = getattr(error, "status_code", None)
            row["note"] = f"ERROR {type(error).__name__}; status={status}"
            log(row["note"])

        log(f"Completed model calls={meter.iters}; tokens={meter.tokens}")
        log(f"[RESULT] {json.dumps(row)}")
        log(f"Log file: {log_path.name}")

    needs_header = (
        not results_path.exists() or results_path.stat().st_size == 0
    )
    with results_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)

    return succeeded


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "condition",
        choices=["baseline", "homogeneous", "overconfident"],
    )
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if PROVIDER != "openai" or MODEL != "gpt-4o-mini":
        parser.error("This experiment expects openai and gpt-4o-mini")

    for _ in range(args.runs):
        if not run_once(args.condition):
            raise SystemExit("Run failed and was saved. Stop and inspect.")