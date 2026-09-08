"""Week 02 starter — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3]

Reads the task and the success criterion from TASK.md, runs each harness
--runs times, judges every run, appends one line per run to results.csv,
and saves each run's console output under logs/. Failed runs are kept:
they are data.

실험 조건(제공자, 모델, 도구 스키마, 시스템 프롬프트, 하네스 상한, 과제)은
conditions.py가 배치 시작 시 conditions/<지문>.json에 저장하고, 각 런 로그
맨 위와 results.csv의 note 칼럼(cond=<지문>)에 같은 지문을 남긴다.
"""
import argparse
import csv
import os
import re
import time
from pathlib import Path

import conditions
from harness_plan_execute import run_plan_execute
from harness_react import run_react

HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]


def read_task(path="TASK.md"):
    text = Path(path).read_text(encoding="utf-8")
    task = re.search(r"^task:\s*(.+)$", text, flags=re.M)
    expected = re.search(r"^expected:\s*(.+)$", text, flags=re.M)
    if not task or not expected:
        raise SystemExit("TASK.md needs a 'task:' line and an 'expected:' line")
    return task.group(1).strip(), expected.group(1).strip()


def judge(answer: str, expected: str) -> bool:
    """Success = the expected string appears in the final answer. Fix the
    criterion in TASK.md before running; do not loosen it afterwards."""
    return expected.lower() in (answer or "").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    task, expected = read_task()
    record = conditions.collect(task, expected, args.runs)
    cond_path = conditions.save(record)
    print(f"[cond] fingerprint={record['fingerprint']} -> {cond_path}")
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
        for name, fn in (("react", run_react), ("plan_exec", run_plan_execute)):
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                for line in conditions.banner(record, name, run_no):
                    log(line)

                t0 = time.time()
                note = conditions.note(record, name)
                try:
                    out = fn(task, log=log)
                    answer, meter = out[0], out[1]
                    if name == "plan_exec":
                        note = f"{note} replans={out[2]}"
                except Exception as e:            # a crash is a failed run, not a lost run
                    answer, meter = "", None
                    note = f"{note} crash: {type(e).__name__}: {e}"
                    log(note)
                success = judge(answer, expected)
                log(f"[final] {answer.strip()[:300]}")
                log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                    f"({time.time() - t0:.1f}s)")

                Path("logs", f"{name}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                w.writerow([run_no, name, "O" if success else "X",
                            meter.tokens if meter else "",
                            meter.iters if meter else "",
                            meter.interventions if meter else "", note])
                f.flush()
    print("\nresults.csv updated;", os.path.abspath("results.csv"))


if __name__ == "__main__":
    main()
