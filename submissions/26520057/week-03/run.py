"""Runner: N runs per condition. One results.csv line and one log file per run.

    python run.py --runs 3                       # all three conditions
    python run.py --condition overconfident --runs 1
"""

import argparse
import csv
import json
import platform
import re
from datetime import datetime
from pathlib import Path

import contract_net as cn

HERE = Path(__file__).parent
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]


def next_run_number(results: Path) -> int:
    if not results.is_file():
        return 1
    with results.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))[1:]
    return len(rows) + 1


def one_run(run: int, condition: str, tasks: list, results: Path):
    log_path = HERE / "logs" / f"run{run:02d}-{condition}.txt"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        def log(line=""):
            print(line)
            fh.write(line + "\n")

        log(f"provider={cn.PROVIDER} model={cn.MODEL} temperature={cn.TEMPERATURE} "
            f"max_tokens={cn.MAX_TOKENS} python={platform.python_version()}")
        log(f"run={run} condition={condition} started={datetime.now().isoformat(timespec='seconds')}")
        for c in cn.make_contractors(condition):
            log(f"SYSTEM {c.name}: {c.system_prompt()}")

        try:
            k = cn.run_round(condition, tasks, log)
            row = [run, condition, k["tasks"], k["correct"], k["messages"],
                   k["unassigned"], k["misawards"], f"parse_fails={k['parse_fails']}"]
        except Exception as e:  # crashed runs stay, with blank counts
            # provider errors echo the masked key (first 8 and last 4 chars); keep it out of logs
            msg = re.sub(r"sk-[\w*-]+", "sk-<redacted>", f"{type(e).__name__}: {e}")
            log(f"CRASH: {msg}")
            row = [run, condition, len(tasks), "", "", "", "", f"crash: {msg}"]
        log(f"\nRESULT {dict(zip(HEADER, row))}")

    new = not results.is_file()
    with results.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=cn.CONDITIONS)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    results = HERE / "results.csv"
    conditions = [args.condition] if args.condition else list(cn.CONDITIONS)
    for condition in conditions:
        for _ in range(args.runs):
            one_run(next_run_number(results), condition, tasks, results)


if __name__ == "__main__":
    main()
