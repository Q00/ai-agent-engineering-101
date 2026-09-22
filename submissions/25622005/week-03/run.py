"""Week 03 runner — conditions x runs, one row per run in results.csv.

    python run.py                              # 3 runs per condition, 9 in total
    python run.py --runs 1 --conditions baseline   # one round, to check the shape

Follows week-02's run_ab.py: the runner writes each run's console output to
logs/ itself (no `tee` needed), appends one line per run to results.csv, and
keeps crashed runs as rows with blank counts and the error in `note`.
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from contractor import (CONDITIONS, MAX_TOKENS, MODEL, PROVIDER,
                        TEMPERATURE_SENT, Meter, build_team)
from manager import run_round

HERE = Path(__file__).parent
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def base_url() -> str:
    var = "ANTHROPIC_BASE_URL" if PROVIDER == "anthropic" else "OPENAI_BASE_URL"
    return os.environ.get(var, "(provider default)")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS),
                    choices=CONDITIONS)
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    args = ap.parse_args()

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    (HERE / "logs").mkdir(exist_ok=True)
    results = HERE / "results.csv"
    new_file = not results.exists()
    run_no = 0
    if not new_file:
        with results.open(encoding="utf-8") as f:
            run_no = sum(1 for _ in f) - 1          # minus the header

    settings = (f"provider={PROVIDER} base_url={base_url()} model={MODEL} "
                f"temperature={TEMPERATURE_SENT} max_tokens={MAX_TOKENS} "
                f"tasks={len(tasks)}")

    with results.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for condition in args.conditions:
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                log(f"{settings} condition={condition} run={run_no}")
                team = build_team(condition)
                for c in team:
                    log(f"  [team] {c.name}: skill={c.skill!r} "
                        f"overconfident={c.overconfident}")

                meter = Meter()
                try:
                    r = run_round(tasks, team, meter, log=log)
                except Exception as e:        # a crash is a failed run, not a lost run
                    note = _KEY.sub("sk-***", f"crash: {type(e).__name__}: {e}")[:300]
                    log(note)
                    row = [run_no, condition, "", "", "", "", "", note]
                else:
                    note = (f"parse_fails={r.parse_fails} tokens={meter.tokens} "
                            f"calls={meter.calls}")
                    log(f"[result] correct={r.correct}/{r.tasks} "
                        f"messages={r.messages} unassigned={r.unassigned} "
                        f"misawards={r.misawards} {note}")
                    row = [run_no, condition, r.tasks, r.correct, r.messages,
                           r.unassigned, r.misawards, note]

                (HERE / "logs" / f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                w.writerow(row)
                f.flush()

    print(f"\nresults.csv updated; {results}")


if __name__ == "__main__":
    main()
