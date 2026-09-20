"""Week 03 runner — one condition, one run, one row appended to results.csv.

    python run.py --condition baseline 2>&1 | tee logs/baseline-1.log

The first printed line carries provider, model, temperature and max_tokens, so
each log file states the settings it was produced under.

A crashed run is written too, with blank counts and the error in `note`
(week-03 README: crashed runs stay).
"""
import argparse
import csv
import json
import os
import re
import sys
import traceback
from pathlib import Path

from contractor import (CONDITIONS, MAX_TOKENS, MODEL, PROVIDER, TEMPERATURE,
                        Meter, build_team)
from manager import run_round

HERE = Path(__file__).parent
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def next_run_id(results: Path) -> int:
    if not results.is_file():
        return 1
    with results.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    return max(len(rows) - 1, 0) + 1          # minus the header


def append_row(results: Path, row):
    new = not results.is_file()
    with results.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)   # keep tee's ordering honest
    p = argparse.ArgumentParser()
    p.add_argument("--condition", required=True, choices=CONDITIONS)
    p.add_argument("--tasks", default=str(HERE / "tasks.json"))
    p.add_argument("--results", default=str(HERE / "results.csv"))
    p.add_argument("--run", type=int, help="run id; defaults to the next free one")
    args = p.parse_args()

    results = Path(args.results)
    run_id = args.run or next_run_id(results)
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))

    base_url = os.environ.get(
        "ANTHROPIC_BASE_URL" if PROVIDER == "anthropic" else "OPENAI_BASE_URL",
        "(provider default)")
    print(f"provider={PROVIDER} base_url={base_url} "
          f"model={MODEL} temperature={TEMPERATURE} max_tokens={MAX_TOKENS} "
          f"condition={args.condition} run={run_id} tasks={len(tasks)}")

    team = build_team(args.condition)
    for c in team:
        print(f"  [team] {c.name}: skill={c.skill!r} overconfident={c.overconfident}")

    meter = Meter()
    try:
        r = run_round(tasks, team, meter)
    except Exception as e:                     # the run still gets a row
        traceback.print_exc()
        note = _KEY.sub("sk-***", f"CRASH {type(e).__name__}: {e}")[:200]
        append_row(results, [run_id, args.condition, "", "", "", "", "", note])
        print(f"\ncrashed; wrote a blank row to {results.name}")
        return 1

    note = f"parse_fails={r.parse_fails} tokens={meter.tokens} calls={meter.calls}"
    append_row(results, [run_id, args.condition, r.tasks, r.correct, r.messages,
                         r.unassigned, r.misawards, note])
    print(f"\ncorrect={r.correct}/{r.tasks} messages={r.messages} "
          f"unassigned={r.unassigned} misawards={r.misawards} {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
