"""Week 02 - the A/B driver.

Reads the task and the success criterion from TASK.md, runs both harnesses the
same number of times on it, judges every run against the criterion that was
committed before any run happened, appends one row per run to results.csv and
saves one console capture per run under logs/.

    py run_ab.py --dry-run            scripted model, spends no requests
    py run_ab.py --runs 3             the graded six runs
    py run_ab.py --runs 1 --only react

Rows are appended, never replaced. A failed run stays in the file.
"""
import argparse
import csv
import io
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime

import tools_shared
from tools_shared import utf8_console

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
LOGS = os.path.join(HERE, "logs")
HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]


def read_task(path=None):
    """The task: and expected: lines of TASK.md, anchored at line start so the
    words appearing in the surrounding prose cannot be picked up by mistake."""
    text = open(path or os.path.join(HERE, "TASK.md"), encoding="utf-8").read()
    got = {}
    for key in ("task", "expected"):
        m = re.search(r"^%s:[ \t]*(.+)$" % key, text, re.M)
        if not m:
            raise SystemExit("TASK.md has no %s: line" % key)
        got[key] = m.group(1).strip()
    return got["task"], got["expected"]


def judge(answer, expected):
    """TASK.md: O when the final answer contains the expected hour in HH:00
    form, X otherwise. A run that ended with no answer at all is X."""
    return "O" if answer and expected in answer else "X"


class Tee(io.TextIOBase):
    """Console capture. Everything a run prints also lands in its log file."""

    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, s):
        self.file.write(s)
        self.stdout.write(s)
        return len(s)

    def flush(self):
        self.file.flush()
        self.stdout.flush()

    def close(self):
        self.file.close()


def next_run_number():
    if not os.path.isfile(RESULTS):
        return 1
    with open(RESULTS, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if r and r[0].strip().isdigit()]
    return max((int(r[0]) for r in rows), default=0) + 1


def append_row(row):
    new = not os.path.isfile(RESULTS)
    with open(RESULTS, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def one_run(run_no, harness, task, expected, dry_run):
    """Run a single arm once. Returns the results.csv row."""
    # A dry run must not drop files into the graded logs/ directory.
    log_dir = tempfile.mkdtemp(prefix="week02-dryrun-") if dry_run else LOGS
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "%s-%02d.txt" % (harness, run_no))
    tee = Tee(log_path)
    old_stdout = sys.stdout
    sys.stdout = tee

    answer, note = None, ""
    # The driver owns the meter, so a crash mid-run still reports the tokens
    # that were already spent instead of silently recording 0.
    meter = tools_shared.Meter()
    try:
        print("run       : %d" % run_no)
        print("harness   : %s" % harness)
        print("model     : %s%s" % (tools_shared.MODEL, "  (DRY RUN, no API)" if dry_run else ""))
        print("started   : %s" % datetime.now().isoformat(timespec="seconds"))
        print("task      : %s" % task)
        print("expected  : %s" % expected)
        print("=" * 70)

        if harness == "react":
            import harness_react as arm
            if dry_run:
                tools_shared.use_fake_model(arm.DRY_RUN_SCRIPT)
            answer, meter, note = arm.run_react(task, meter=meter)
        else:
            import harness_plan_execute as arm
            if dry_run:
                tools_shared.use_fake_model(arm.DRY_RUN_SCRIPT)
            answer, meter, note = arm.run_plan_execute(task, meter=meter)
    except Exception as e:
        note = "crashed: %s: %s" % (type(e).__name__, e)
        traceback.print_exc(file=tee)
    finally:
        success = judge(answer, expected)
        print("=" * 70)
        print("answer    : %r" % answer)
        print("success   : %s  (expected %r in the answer)" % (success, expected))
        print("tokens    : %d" % meter.tokens)
        print("iters     : %d" % meter.iters)
        print("interventions: %d" % meter.interventions)
        print("note      : %s" % note)
        sys.stdout = old_stdout
        tee.close()

    print("  run %-2d %-10s %s  tokens=%-6d iters=%-3d  %s"
          % (run_no, harness, success, meter.tokens, meter.iters, note))
    return [run_no, harness, success, meter.tokens, meter.iters,
            meter.interventions, note]


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per harness")
    ap.add_argument("--dry-run", action="store_true",
                    help="scripted model, no API requests")
    ap.add_argument("--only", choices=["react", "plan_exec"],
                    help="run a single arm")
    args = ap.parse_args()

    task, expected = read_task()
    arms = [args.only] if args.only else ["react", "plan_exec"]

    print("task     : %s" % task)
    print("expected : %s" % expected)
    print("model    : %s%s" % (tools_shared.MODEL, "  (DRY RUN)" if args.dry_run else ""))
    print("arms     : %s x %d run(s)" % (", ".join(arms), args.runs))
    print("")

    run_no = next_run_number()
    rows = []
    for arm in arms:
        for _ in range(args.runs):
            row = one_run(run_no, arm, task, expected, args.dry_run)
            rows.append(row)
            # Append as each run finishes. Writing all six at the end means an
            # interruption halfway through loses every measurement taken so
            # far, along with the requests that paid for them.
            if not args.dry_run:
                append_row(row)
            run_no += 1

    if args.dry_run:
        print("\ndry run: results.csv and logs/ were NOT written")
        for r in rows:
            print("  " + ",".join(str(c) for c in r))
        return

    print("\nappended %d row(s) to results.csv" % len(rows))


if __name__ == "__main__":
    main()
