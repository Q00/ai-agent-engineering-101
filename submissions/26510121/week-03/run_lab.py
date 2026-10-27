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


def paths(fake: bool):
    """A stub run must never land in results.csv: the file is the submission."""
    suffix = "-fake" if fake else ""
    return (HERE / ("results%s.csv" % suffix),
            HERE / ("logs%s" % suffix),
            HERE / ("bids%s" % suffix))


def next_run_no(results: Path) -> int:
    if not results.exists():
        return 1
    with results.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    return max((int(r[0]) for r in rows[1:] if r[0].strip().isdigit()), default=0) + 1


def append_row(results: Path, row):
    new = not results.exists()
    with results.open("a", encoding="utf-8", newline="") as f:
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
    results, logs, bids_dir = paths(args.fake)
    logs.mkdir(exist_ok=True)
    bids_dir.mkdir(exist_ok=True)

    if args.fake:
        from fake_provider import make_fake_ask
        ask = make_fake_ask()
        header = "provider=fake (deterministic stub, counting check only)"
    else:
        ask = chat.ask
        header = None                  # written after the run: see settings_line

    run_no = next_run_no(results)
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
            (logs / f"{run_no:02d}-{condition}.txt").write_text(
                settings + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
            with (bids_dir / f"{run_no:02d}-{condition}.jsonl").open(
                    "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            append_row(results, row)
            run_no += 1

    print(f"\nwrote {results.name}; logs in {logs.name}/, bid records in {bids_dir.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
