"""Runner for the week-03 contract net.

Runs each condition N times over the same task set, appends one row per run to
results.csv, and writes one console-capture log per run under logs/.

    python run.py --runs 3                 # real model (needs an API key)
    python run.py --runs 3 --dry           # offline stub, no key (plumbing only)

results.csv is appended, not replaced — re-running adds rows. Crashed runs are
kept with blank counts and the error in the note column.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

import contract_net
import llm

HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
CONDITIONS = ["baseline", "homogeneous", "overconfident"]


def next_run_index(csv_path):
    """Continue run numbering across appends."""
    if not os.path.exists(csv_path):
        return 1
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if r and r != HEADER]
    return len(rows) + 1


def make_logger(lines):
    def log(msg=""):
        print(msg)
        lines.append(str(msg))
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per condition")
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    ap.add_argument("--tasks", default="tasks.json")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--dry", action="store_true", help="offline stub, no API key")
    args = ap.parse_args()

    if not args.dry and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print("no API key in environment (ANTHROPIC_API_KEY / OPENAI_API_KEY).")
        print("set one, or use --dry to test the harness offline.")
        return 1

    with open(args.tasks, encoding="utf-8") as f:
        tasks = json.load(f)
    os.makedirs(args.logs, exist_ok=True)

    new_file = not os.path.exists(args.out)
    out = open(args.out, "a", encoding="utf-8", newline="")
    writer = csv.writer(out)
    if new_file:
        writer.writerow(HEADER)

    run_idx = next_run_index(args.out)
    print(f"provider={llm.PROVIDER} model={llm.MODEL} temp={llm.TEMPERATURE} "
          f"dry={args.dry} runs/condition={args.runs}\n")

    for condition in args.conditions:
        for _ in range(args.runs):
            lines = []
            log = make_logger(lines)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log(f"# run={run_idx} condition={condition} model={llm.MODEL} "
                f"temp={llm.TEMPERATURE} dry={args.dry} time={stamp}")
            try:
                c = contract_net.run_condition(condition, tasks, dry=args.dry, log=log)
                writer.writerow([run_idx, condition, c["tasks"], c["correct"],
                                 c["messages"], c["unassigned"], c["misawards"], c["note"]])
            except Exception as e:                       # keep crashed runs as evidence
                log(f"CRASH: {e}")
                writer.writerow([run_idx, condition, "", "", "", "", "",
                                 f"CRASH: {type(e).__name__}: {e}"])
            out.flush()
            logfile = os.path.join(args.logs, f"{condition}-run{run_idx}-{stamp}.txt")
            with open(logfile, "w", encoding="utf-8") as lf:
                lf.write("\n".join(lines) + "\n")
            print(f"  [log] {logfile}\n")
            run_idx += 1

    out.close()
    print(f"done. rows appended to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
