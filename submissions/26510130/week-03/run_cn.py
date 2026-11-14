"""Week 03 runner — run the three conditions and record results.csv.

Usage: python run_cn.py [--runs 3] [--conditions baseline,homogeneous,overconfident]

Reads tasks.json, runs each condition --runs times, appends one row per run to
results.csv, and writes each run's console capture to logs/. A crashed run is
kept: blank counts, the error in `note`.
"""
import argparse
import csv
import json
import time
from pathlib import Path

import net

HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned",
          "misawards", "note"]
CONDITIONS = ("baseline", "homogeneous", "overconfident")


def next_run_no() -> int:
    p = Path("results.csv")
    if not p.exists():
        return 0
    with p.open(encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    args = ap.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for c in conditions:
        if c not in CONDITIONS:
            raise SystemExit(f"unknown condition: {c}")

    tasks = json.loads(Path("tasks.json").read_text(encoding="utf-8"))
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    run_no = next_run_no()

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for condition in conditions:
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _l=lines):
                    print(msg)
                    _l.append(str(msg))

                contractors = net.build(condition)
                log(f"[run {run_no}] condition={condition} model={net.MODEL} "
                    f"temperature={net.TEMPERATURE}")
                for c in contractors:
                    log(f"  [contractor] {c.name}: {c.system.splitlines()[0]}")

                t0 = time.time()
                try:
                    res, meter = net.run_net(tasks, contractors, log)
                    row = [run_no, condition, res.tasks, res.correct,
                           meter.messages, res.unassigned, res.misawards,
                           f"{net.MODEL} unparseable={res.unparseable} "
                           f"calls={meter.calls} tokens={meter.tokens}"]
                except Exception as e:      # a crash is a failed run, not a lost run
                    log(f"[crash] {type(e).__name__}: {e}")
                    row = [run_no, condition, "", "", "", "", "",
                           f"{net.MODEL} crash: {type(e).__name__}: {e}"]
                log(f"[done] {time.time() - t0:.1f}s")

                Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                w.writerow(row)
                f.flush()

    print("\nresults.csv updated;", Path("results.csv").resolve())


if __name__ == "__main__":
    main()
