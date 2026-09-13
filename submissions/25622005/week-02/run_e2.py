"""E2 — axis 3 in isolation: terminate on the answer, not on plan exhaustion.

Runs only the Plan-then-Execute harness with early_exit=True, on the task in
TASK.md, and appends rows to results.csv exactly as run_ab.py does. The
harness column stays 'plan_exec' (CI accepts only react|plan_exec); the
variant is recorded in the note column and in the log file names.

Usage: python run_e2.py [--runs 3]
"""
import argparse
import csv
import time
from pathlib import Path

from harness_plan_execute import run_plan_execute
from run_ab import HEADER, judge, read_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    task, expected = read_task()
    Path("logs").mkdir(exist_ok=True)
    with open("results.csv", encoding="utf-8") as f:
        run_no = sum(1 for _ in f) - 1

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for _ in range(args.runs):
            run_no += 1
            lines = []

            def log(msg, _lines=lines):
                print(msg)
                _lines.append(str(msg))

            t0 = time.time()
            try:
                answer, meter, replans = run_plan_execute(
                    task, early_exit=True, log=log)
                note = f"replans={replans} variant=early_exit"
            except Exception as e:
                answer, meter = "", None
                note = f"crash: {type(e).__name__}: {e} variant=early_exit"
                log(note)
            success = judge(answer, expected)
            log(f"[final] {answer.strip()[:300]}")
            log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                f"({time.time() - t0:.1f}s)")

            Path("logs", f"plan_exec_early-{run_no:02d}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            w.writerow([run_no, "plan_exec", "O" if success else "X",
                        meter.tokens if meter else "",
                        meter.iters if meter else "",
                        meter.interventions if meter else "", note])
            f.flush()


if __name__ == "__main__":
    main()
