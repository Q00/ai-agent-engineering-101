"""Week 02 starter — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3]

Reads the task and the success criterion from TASK.md, runs each harness
--runs times, judges every run, appends one line per run to results.csv,
and saves each run's console output under logs/. Failed runs are kept:
they are data.
"""
import argparse
import csv
import os
import re
import time
from pathlib import Path

from harness_plan_execute import run_plan_execute
from harness_react import run_react
from tools_shared import MODEL, PROVIDER

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

                log(f"[config] provider={PROVIDER} model={MODEL}")

                t0 = time.time()
                note = ""
                try:
                    out = fn(task, log=log)
                    answer, meter = out[0], out[1]
                    extra = out[2] if len(out) > 2 else {}
                    note = (",".join(f"{k}={v}" for k, v in extra.items())
                            if isinstance(extra, dict) else f"replans={extra}")
                except Exception as e:            # a crash is a failed run, not a lost run
                    answer, meter, note = "", None, f"crash: {type(e).__name__}: {e}"
                    log(note)
                if meter is not None:             # provider-neutral cost proxy
                    note = ",".join(x for x in (note, f"prompt_chars={meter.prompt_chars}") if x)
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
