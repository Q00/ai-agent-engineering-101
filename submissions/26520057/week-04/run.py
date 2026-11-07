"""Runner. A run = one condition x one repeat over all scenarios.
One results.csv line per episode, one log file per run.

    python run.py                          # runs 1-9: free 1-3, tagged 4-6, structured 7-9
    python run.py --condition tagged       # only runs 4-6
    python run.py --first-run 10           # a fresh set numbered 10-18

Interrupted? Run the same command again: (run, scenario) pairs already in
results.csv are skipped, including crashed ones, which stay as evidence.
"""

import argparse
import csv
import json
import platform
import re
from datetime import datetime
from pathlib import Path

import negotiation as ng

HERE = Path(__file__).parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]


def done_pairs(results: Path) -> set:
    if not results.is_file():
        return set()
    with results.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def append_row(results: Path, row: list):
    new = not results.is_file()
    with results.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def one_run(run: int, condition: str, scenarios: list, results: Path):
    todo = [s for s in scenarios if (str(run), str(s["id"])) not in done_pairs(results)]
    if not todo:
        return
    log_path = HERE / "logs" / f"run{run:02d}-{condition}.txt"
    log_path.parent.mkdir(exist_ok=True)
    resumed = log_path.exists()
    with log_path.open("a", encoding="utf-8") as fh:
        def log(line=""):
            print(line, flush=True)
            fh.write(line + "\n")
            fh.flush()

        log(f"provider={ng.PROVIDER} model={ng.MODEL} temperature={ng.TEMPERATURE} "
            f"agent_max_tokens={ng.AGENT_MAX_TOKENS} reader_max_tokens={ng.READER_MAX_TOKENS} "
            f"turn_limit={ng.TURN_LIMIT} python={platform.python_version()}")
        log(f"run={run} condition={condition} {'RESUMED' if resumed else 'started'}="
            f"{datetime.now().isoformat(timespec='seconds')} scenarios={[s['id'] for s in todo]}")

        for s in todo:
            try:
                k = ng.run_episode(condition, s, log)
                row = [run, condition, s["id"], k["deal_possible"], k["outcome"],
                       "" if k["price"] is None else k["price"], k["correct"], k["violation"],
                       k["turns"], k["format_errors"], k["reader_calls"], k["note"]]
            except Exception as e:  # crashed episodes stay, with blank fields
                # provider errors echo the masked key; keep it out of logs and the CSV
                msg = re.sub(r"sk-[\w*-]+", "sk-<redacted>", f"{type(e).__name__}: {e}")
                log(f"CRASH {s['id']}: {msg}")
                row = [run, condition, s["id"], int(s["reserve"] <= s["budget"]),
                       "", "", "", "", "", "", "", f"crash: {msg}"]
            append_row(results, row)
            log(f"ROW {dict(zip(HEADER, row))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=ng.CONDITIONS)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--first-run", type=int, default=1)
    args = ap.parse_args()

    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    results = HERE / "results.csv"
    for ci, condition in enumerate(ng.CONDITIONS):  # run numbers stay fixed per condition
        if args.condition and condition != args.condition:
            continue
        for k in range(args.repeats):
            one_run(args.first_run + ci * args.repeats + k, condition, scenarios, results)


if __name__ == "__main__":
    main()
