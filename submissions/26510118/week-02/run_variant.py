"""Week 02 — run one harness with non-default axis values and append to results.csv.

The two harnesses already expose their axis values as parameters; run_ab.py just
never passes them. This runner does, so a variant needs no edit to the harness
files (they stay byte-identical to the starter).

Usage:
  python run_variant.py --harness plan_exec --runs 3 --max-tool-rounds 9
  python run_variant.py --harness react     --runs 3 --max-steps 4

Rows are appended with the varied value recorded in the `note` column, because
check_week02.py only accepts `react` and `plan_exec` in the `harness` column.
"""
import argparse
import csv
import time
from pathlib import Path

from harness_plan_execute import run_plan_execute
from harness_react import run_react
from run_ab import HEADER, judge, read_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=["react", "plan_exec"], required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=None, help="react only")
    ap.add_argument("--max-tool-rounds", type=int, default=None, help="plan_exec only")
    ap.add_argument("--max-replan", type=int, default=None, help="plan_exec only")
    args = ap.parse_args()

    task, expected = read_task()
    Path("logs").mkdir(exist_ok=True)

    kwargs, tag = {}, []
    if args.harness == "react":
        if args.max_steps is not None:
            kwargs["max_steps"] = args.max_steps
            tag.append(f"max_steps={args.max_steps}")
        fn = run_react
    else:
        if args.max_tool_rounds is not None:
            kwargs["max_tool_rounds"] = args.max_tool_rounds
            tag.append(f"max_tool_rounds={args.max_tool_rounds}")
        if args.max_replan is not None:
            kwargs["max_replan"] = args.max_replan
            tag.append(f"max_replan={args.max_replan}")
        fn = run_plan_execute
    if not tag:
        raise SystemExit("pass at least one axis value, or use run_ab.py for the defaults")
    variant = " ".join(tag)

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

            log(f"[variant] {args.harness} {variant}")
            t0 = time.time()
            note = variant
            try:
                out = fn(task, log=log, **kwargs)
                answer, meter = out[0], out[1]
                if args.harness == "plan_exec":
                    note = f"{variant} replans={out[2]}"
            except Exception as e:          # a crash is a failed run, not a lost run
                answer, meter = "", None
                note = f"{variant} crash: {type(e).__name__}: {e}"
                log(note)
            success = judge(answer, expected)
            log(f"[final] {answer.strip()[:300]}")
            log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                f"({time.time() - t0:.1f}s)")

            Path("logs", f"{args.harness}-{run_no:02d}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            w.writerow([run_no, args.harness, "O" if success else "X",
                        meter.tokens if meter else "",
                        meter.iters if meter else "",
                        meter.interventions if meter else "", note])
            f.flush()
    print(f"\nappended {args.runs} run(s) for {args.harness} [{variant}]")


if __name__ == "__main__":
    main()
