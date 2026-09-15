"""Week 02 — axis-3 ablation runner.

Same model, same task, same tools, same Plan-then-Execute harness as the
baseline. One thing changes: run_plan_execute(early_exit=True), so the run
stops at the first step that produces an answer instead of marching to the
end of the plan. Everything else -- context split, tool set, replan budget,
intervention hook -- is untouched, so a baseline/ablation gap isolates the
termination condition (axis 3).

Rows are appended to the same results.csv with harness="plan_exec" (the CI
checker only accepts react|plan_exec) and note="early_exit=1", which is how
the ablation rows are told apart from the baseline rows.

Usage: python run_ablation.py [--runs 3]
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
    new_file = not Path("results.csv").exists()
    run_no = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            run_no = sum(1 for _ in f) - 1

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
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
            note = "early_exit=1"
            try:
                answer, meter, replans = run_plan_execute(
                    task, early_exit=True, log=log)
                note = f"early_exit=1 replans={replans}"
            except Exception as e:
                answer, meter = "", None
                note = f"early_exit=1 crash: {type(e).__name__}: {e}"
                log(note)
            success = judge(answer, expected)
            log(f"[final] {answer.strip()[:300]}")
            log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                f"({time.time() - t0:.1f}s)")

            Path("logs", f"plan_exec_ee-{run_no:02d}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            w.writerow([run_no, "plan_exec", "O" if success else "X",
                        meter.tokens if meter else "",
                        meter.iters if meter else "",
                        meter.interventions if meter else "", note])
            f.flush()
    print("\nresults.csv updated")


if __name__ == "__main__":
    main()
