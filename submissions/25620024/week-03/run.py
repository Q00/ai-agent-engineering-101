"""Week 03 — run.py: build a team per condition, run 3x each, log everything.

Usage: python run.py [condition ...]
  no args        -> all of CONDITIONS
  one or more    -> only those conditions (e.g. "python run.py reputation"
                    to add a new condition without re-running the others)
Needs ANTHROPIC_API_KEY (or OPENAI_API_KEY + OPENAI_BASE_URL) in the
environment. Appends to results.csv (run numbers continue from whatever is
already in the file) and writes one log file per run under logs/ (never
overwrites -- crashed and old runs stay as evidence).
"""
import csv
import json
import os
import sys
from datetime import datetime

from contractor import Candidate
from manager import Reputation, run_round
from model import MODEL, PROVIDER, TEMPERATURE, Meter

CONDITIONS = ["baseline", "homogeneous", "overconfident", "reputation"]
RUNS_PER_CONDITION = 3
BASE_SKILLS = {"A": "arithmetic", "B": "writing", "C": "coding"}


def build_team(condition):
    """The one place the three required conditions differ. "reputation" is
    the axis-1+3 extension: same team as baseline, the only difference is
    that run_round() is given a Reputation object (see main())."""
    if condition in ("baseline", "reputation"):
        return [Candidate(name=n, skill=s) for n, s in BASE_SKILLS.items()]
    if condition == "homogeneous":
        return [Candidate(name=n, skill="general problem solving") for n in BASE_SKILLS]
    if condition == "overconfident":
        return [Candidate(name=n, skill=s, overconfident=(n == "C"))
                for n, s in BASE_SKILLS.items()]
    raise ValueError(f"unknown condition: {condition}")


def main(conditions):
    with open("tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)

    os.makedirs("logs", exist_ok=True)
    is_new = not os.path.exists("results.csv")
    if is_new:
        run_number = 0
    else:
        with open("results.csv", encoding="utf-8", newline="") as f:
            run_number = sum(1 for row in csv.reader(f) if row) - 1  # minus header

    csv_file = open("results.csv", "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    if is_new:
        writer.writerow(["run", "condition", "tasks", "correct", "messages",
                          "unassigned", "misawards", "note"])

    for condition in conditions:
        # one Reputation per condition: accumulates across its 3 reps, then
        # discarded -- never leaks into the next condition's comparison
        reputation = Reputation() if condition == "reputation" else None
        for rep in range(1, RUNS_PER_CONDITION + 1):
            run_number += 1
            team = build_team(condition)
            meter = Meter()
            lines = [f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE}"]

            def log(msg, _lines=lines):
                print(msg)
                _lines.append(msg)

            log(f"=== run {run_number}: {condition} (rep {rep}) ===")
            try:
                result = run_round(tasks, team, meter, log=log, reputation=reputation)
                note = f"parse_fails={result.parse_fails}"
                writer.writerow([run_number, condition, result.tasks, result.correct,
                                  result.messages, result.unassigned, result.misawards, note])
            except Exception as e:                     # crashed run: keep the row, blank counts
                log(f"CRASH: {e}")
                writer.writerow([run_number, condition, "", "", "", "", "", f"crash: {e}"])
            csv_file.flush()

            stamp = datetime.now().strftime("%m%d-%H%M%S")
            with open(f"logs/{condition}-{rep}-{stamp}.txt", "w", encoding="utf-8") as lf:
                lf.write("\n".join(lines) + "\n")

    csv_file.close()


if __name__ == "__main__":
    main(sys.argv[1:] or CONDITIONS)
