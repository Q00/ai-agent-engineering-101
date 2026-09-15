"""Week 03 — run the three conditions and record results.csv and logs/.

Usage: python run.py [--runs 3]

This is the only module that knows the condition names. `make_contractors()` is
the entire independent variable: the announcement, the bid prompt, the award
rule, and the counting are identical across conditions, so anything that moves
between them came from that one function.

Conditions run grouped (all baseline, then all homogeneous, then all
overconfident). Rows are appended, never replaced; a crashed run keeps its row
with blank counts and the error in `note`.
"""
import argparse
import csv
import json
import time
from pathlib import Path

from contractor import Contractor
from manager import run_round
from model import Meter, settings_line

CONDITIONS = ("baseline", "homogeneous", "overconfident")
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]

SKILLS = {"A": "arithmetic", "B": "writing", "C": "coding"}
GENERALIST = "general problem solving"


def make_contractors(condition: str):
    """The independent variable. Nothing else differs between conditions."""
    if condition == "baseline":
        return [Contractor(n, SKILLS[n]) for n in SKILLS]
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in SKILLS]
    if condition == "overconfident":
        # baseline plus one sentence on C's system prompt (see contractor.py)
        return [Contractor(n, SKILLS[n], overconfident=(n == "C")) for n in SKILLS]
    raise ValueError(f"unknown condition: {condition}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tasks = json.loads(Path("tasks.json").read_text(encoding="utf-8"))
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

        for condition in CONDITIONS:
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                log(settings_line())          # first line: provider, model, temperature
                log(f"condition={condition} run={run_no} tasks={len(tasks)}")
                team = make_contractors(condition)
                for c in team:
                    log(f"  contractor {c.name}: skill={c.skill!r} "
                        f"overconfident={c.overconfident}")

                meter = Meter()
                t0 = time.time()
                try:
                    r = run_round(tasks, team, meter, log=log)
                    note = (f"parse_fails={r.parse_fails} "
                            f"calls={meter.calls} tokens={meter.tokens}")
                    row = [run_no, condition, r.tasks, r.correct, r.messages,
                           r.unassigned, r.misawards, note]
                except Exception as e:        # a crash is a failed run, not a lost run
                    note = f"crash: {type(e).__name__}: {e}"
                    log(note)
                    row = [run_no, condition, "", "", "", "", "", note]

                log(f"[done] {time.time() - t0:.1f}s  {note}")
                Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                w.writerow(row)
                f.flush()

    print(f"\nresults.csv updated: {Path('results.csv').resolve()}")


if __name__ == "__main__":
    main()
