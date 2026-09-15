"""Runner: three conditions, N rounds each, appended to results.csv.

Steps 4 and 5 of the lab. Step 4 — separating the conditions — already lives
in contractor.build_team, which is the only place a condition name turns into
three contractors. This file's job is to hand it a condition name, run a
round, and write down what happened.

Three rules the assignment is explicit about, implemented here:

  Rows are appended, never replaced. Run the file again and the new runs join
  the old ones, as in week-02's run_ab.py. Run numbers continue from what is
  already in results.csv rather than restarting at 1.

  A crashed run stays. It gets its row with the reason in the note column and
  whatever metrics the Meter had reached. Week 02 lost the token counts of two
  rate-limited runs this way and had to report an undercount; here the meter
  is read before the exception is handled, so a crash keeps its numbers.

  Every run gets one log file whose first line says what produced it —
  provider, model, and the sampling settings, including the fact that
  temperature is not settable on this model.
"""

import argparse
import csv
import io
import os
import sys
import time
import traceback

from contractor import CONDITIONS, build_team
from manager import load_tasks, run_round
from model import Meter, run_header

HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
RESULTS = "results.csv"
LOGDIR = "logs"


class Tee(io.TextIOBase):
    """Write to the console and to the run's log file at once.

    The log is what the report quotes, so it holds the same text the console
    showed, untruncated. Week 02's runner cut logged replies to 300
    characters and the line that decided a run fell past the cut; the log
    could not show the evidence for its own verdict. Nothing is cut here.
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()
        return len(s)


def next_run_number() -> int:
    if not os.path.exists(RESULTS):
        return 1
    with open(RESULTS, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh) if r and r[0] != "run"]
    nums = []
    for r in rows:
        try:
            nums.append(int(r[0]))
        except (ValueError, IndexError):
            continue
    return max(nums) + 1 if nums else 1


def append_row(row: dict):
    exists = os.path.exists(RESULTS)
    with open(RESULTS, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        if not exists:
            w.writeheader()
        w.writerow(row)


def one_run(run_no: int, condition: str, tasks) -> dict:
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, f"{condition}-{run_no:02d}.txt")
    meter = Meter()
    started = time.time()

    with open(path, "w", encoding="utf-8") as fh:
        out = Tee(sys.stdout, fh)
        log = lambda *a: print(*a, file=out)

        log(run_header())
        log(f"run={run_no} condition={condition} tasks={len(tasks)}")
        for c in build_team(condition):
            log(f"  contractor {c.name}: skill={c.skill!r} "
                f"overconfident={c.overconfident}")
        log("-" * 72)

        try:
            r = run_round(tasks, build_team(condition), meter, log=log)
        except Exception as exc:
            # The meter is read here, before anything else, so a crashed run
            # still reports the tokens and calls it actually spent.
            wall = time.time() - started
            reason = f"{type(exc).__name__}: {exc}"
            log("-" * 72)
            log(f"CRASH after {meter.calls} call(s): {reason}")
            log(traceback.format_exc())
            note = (f"crash: {reason} | tokens={meter.tokens} "
                    f"calls={meter.calls} wall={wall:.1f}s")
            return {"run": run_no, "condition": condition, "tasks": len(tasks),
                    "correct": "", "messages": "", "unassigned": "",
                    "misawards": "", "note": note}

        wall = time.time() - started
        log("-" * 72)
        log(f"tasks={r.tasks} correct={r.correct} messages={r.messages} "
            f"unassigned={r.unassigned} misawards={r.misawards}")
        extra = f"tokens={meter.tokens} calls={meter.calls} wall={wall:.1f}s"
        log(f"note: {r.note(extra)}")
        return {"run": run_no, "condition": condition, "tasks": r.tasks,
                "correct": r.correct, "messages": r.messages,
                "unassigned": r.unassigned, "misawards": r.misawards,
                "note": r.note(extra)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3,
                    help="rounds per condition (assignment asks for 3 or more)")
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS),
                    choices=list(CONDITIONS))
    ap.add_argument("--tasks", default="tasks.json")
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    run_no = next_run_number()
    print(run_header())
    print(f"tasks={len(tasks)} conditions={args.conditions} "
          f"runs_each={args.runs} first_run_number={run_no}")

    for condition in args.conditions:
        for _ in range(args.runs):
            print("\n" + "=" * 72)
            row = one_run(run_no, condition, tasks)
            append_row(row)
            print(f"[appended] run {run_no} ({condition}) -> {RESULTS}")
            run_no += 1


if __name__ == "__main__":
    main()
