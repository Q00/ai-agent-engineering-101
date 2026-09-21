"""Run the three conditions and record them.

Usage:
  python run_lab.py --runs 3                 # live, all three conditions
  python run_lab.py --condition baseline     # one condition only
  python run_lab.py --fake                   # no provider, counting check only

Each run appends one line to results.csv, one console capture to logs/, and
one machine-readable bid record to bids/. A run that crashes still gets its
line, with blank counts and the error in `note`: a crashed run is data.
"""
import argparse
import csv
import json
import sys
import traceback
from pathlib import Path

import chat
from contractor import build_team
from manager import run_round

HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
CONDITIONS = ("baseline", "homogeneous", "overconfident")

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.csv"
LOGS = HERE / "logs"
BIDS = HERE / "bids"


def next_run_no() -> int:
    if not RESULTS.exists():
        return 1
    with RESULTS.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    return max((int(r[0]) for r in rows[1:] if r[0].strip().isdigit()), default=0) + 1


def append_row(row):
    new = not RESULTS.exists()
    with RESULTS.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per condition")
    ap.add_argument("--condition", choices=CONDITIONS, action="append",
                    help="repeatable; default is all three")
    ap.add_argument("--fake", action="store_true",
                    help="use the deterministic stub instead of the provider")
    args = ap.parse_args()

    conditions = args.condition or list(CONDITIONS)
    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    LOGS.mkdir(exist_ok=True)
    BIDS.mkdir(exist_ok=True)

    if args.fake:
        from fake_provider import make_fake_ask
        ask = make_fake_ask()
        header = "provider=fake (deterministic stub, counting check only)"
    else:
        ask = chat.ask
        header = None                  # written after the run: see settings_line

    run_no = next_run_no()
    for condition in conditions:
        for _ in range(args.runs):
            lines = []

            def log(msg, _lines=lines):
                print(msg)
                _lines.append(str(msg))

            meter = chat.Meter()
            team = build_team(condition)
            log(f"=== run {run_no} condition={condition} "
                f"team={[(c.name, c.skill) for c in team]}")

            row, records = None, []
            try:
                r = run_round(tasks, team, meter, ask, condition, run_no, log)
                records = r.records
                note = r.note(f"tokens={meter.tokens} calls={meter.calls}")
                row = [run_no, condition, r.tasks, r.correct, r.messages,
                       r.unassigned, r.misawards, note]
                log(f"\n[result] correct={r.correct}/{r.tasks} messages={r.messages} "
                    f"unassigned={r.unassigned} misawards={r.misawards} {note}")
            except Exception as e:                       # keep the crashed run
                log(traceback.format_exc())
                row = [run_no, condition, "", "", "", "", "",
                       f"crashed: {type(e).__name__}: {str(e)[:160]}"]
                log(f"\n[result] crashed: {type(e).__name__}")

            settings = header or chat.settings_line()
            (LOGS / f"{run_no:02d}-{condition}.txt").write_text(
                settings + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
            with (BIDS / f"{run_no:02d}-{condition}.jsonl").open(
                    "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            append_row(row)
            run_no += 1

    print(f"\nwrote {RESULTS.name}; logs in {LOGS.name}/, bid records in {BIDS.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
