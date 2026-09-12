"""Week 02 extension — run the early-exit harness and record results_early.csv.

Kept out of run_ab.py and results.csv on purpose: check_week02.py treats any
harness value other than react|plan_exec as a malformed row, and the eighteen
graded runs should stay exactly as they were produced. Same task, same
success criterion, same judge as run_ab.py — only the harness differs.

Usage: python run_early.py [--runs 3]
"""
import argparse
import csv
import time
from pathlib import Path

from harness_plan_execute_early import run_plan_execute_early
from run_ab import HEADER, judge, read_task
from tools_shared import MODEL, PROVIDER, TOOLSET

RESULTS = "results_early.csv"
LOGDIR = "logs_early"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    task, expected = read_task()
    Path(LOGDIR).mkdir(exist_ok=True)
    new_file = not Path(RESULTS).exists()
    run_no = 0
    if not new_file:
        with open(RESULTS, encoding="utf-8") as f:
            run_no = sum(1 for _ in f) - 1

    with open(RESULTS, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for _ in range(args.runs):
            run_no += 1
            lines = []

            def log(msg, _lines=lines):
                print(msg)
                _lines.append(str(msg))

            t0 = time.time()
            note = f"{PROVIDER}:{MODEL} tools={TOOLSET}"
            try:
                answer, meter, replans = run_plan_execute_early(task, log=log)
                note += f" replans={replans}"
            except Exception as e:
                answer, meter = "", None
                note += f" crash: {type(e).__name__}: {e}"
                log(note)
            success = judge(answer, expected)
            log(f"[final] {answer.strip()[:300]}")
            log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                f"({time.time() - t0:.1f}s)")

            Path(LOGDIR, f"plan_exec_early-{run_no:02d}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            w.writerow([run_no, "plan_exec_early", "O" if success else "X",
                        meter.tokens if meter else "",
                        meter.iters if meter else "",
                        meter.interventions if meter else "", note])
            f.flush()
    print(f"\n{RESULTS} updated")


if __name__ == "__main__":
    main()
