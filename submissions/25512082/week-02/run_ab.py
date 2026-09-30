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
import statistics
import time
from pathlib import Path

from harness_plan_execute import SYSTEM_EXEC, SYSTEM_PLAN, run_plan_execute
from harness_react import SYSTEM as SYSTEM_REACT, run_react
from tools_shared import MAX_OUTPUT_TOKENS, MODEL, PROVIDER, TOOL_SPECS

HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]
REACT_MAX_STEPS = 8
PLAN_MAX_REPLAN = 1
PLAN_MAX_TOOL_ROUNDS = 3
PLAN_MAX_STEPS = 16


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


def summarize(rows):
    """Print success rate and population variance for this experiment batch."""
    print("\n[batch summary]")
    for harness in ("react", "plan_exec"):
        group = [r for r in rows if r["harness"] == harness]
        if not group:
            continue
        successes = sum(r["success"] for r in group)
        print(f"{harness}: success={successes}/{len(group)} "
              f"({successes / len(group):.1%})")
        for metric in ("tokens", "iters", "interventions"):
            values = [r[metric] for r in group if r[metric] is not None]
            if not values:
                print(f"  {metric}: unavailable")
                continue
            variance = statistics.pvariance(values) if len(values) > 1 else 0.0
            print(f"  {metric}: mean={statistics.mean(values):.2f}, "
                  f"variance={variance:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--harness", choices=("both", "react", "plan_exec"), default="both")
    args = ap.parse_args()

    task, expected = read_task()
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    run_no = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            run_no = max((int(r["run"]) for r in rows), default=0)

    batch = []
    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        harnesses = (("react", run_react), ("plan_exec", run_plan_execute))
        for name, fn in harnesses:
            if args.harness != "both" and name != args.harness:
                continue
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                systems = [SYSTEM_REACT] if name == "react" else [SYSTEM_PLAN, SYSTEM_EXEC]
                limits = ({"max_steps": REACT_MAX_STEPS} if name == "react" else
                          {"max_replan": PLAN_MAX_REPLAN,
                           "max_tool_rounds": PLAN_MAX_TOOL_ROUNDS,
                           "max_steps": PLAN_MAX_STEPS})
                log(f"[condition] provider={PROVIDER} model={MODEL}")
                log(f"[condition] max_output_tokens={MAX_OUTPUT_TOKENS}")
                log(f"[condition] task={task}")
                log(f"[condition] system_prompts={systems!r}")
                log(f"[condition] tools={TOOL_SPECS!r}")
                log(f"[condition] limits={limits}")

                t0 = time.time()
                note = ""
                try:
                    out = (fn(task, max_steps=REACT_MAX_STEPS, log=log)
                           if name == "react" else
                           fn(task, max_replan=PLAN_MAX_REPLAN,
                              max_tool_rounds=PLAN_MAX_TOOL_ROUNDS,
                              max_steps=PLAN_MAX_STEPS, log=log))
                    answer, meter = out[0], out[1]
                    if name == "plan_exec":
                        note = f"replans={out[2]}"
                except Exception as e:            # a crash is a failed run, not a lost run
                    answer, meter, note = "", None, f"crash: {type(e).__name__}: {e}"
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
                batch.append({"harness": name, "success": success,
                              "tokens": meter.tokens if meter else None,
                              "iters": meter.iters if meter else None,
                              "interventions": meter.interventions if meter else None})
    summarize(batch)
    print("\nresults.csv updated;", os.path.abspath("results.csv"))


if __name__ == "__main__":
    main()
