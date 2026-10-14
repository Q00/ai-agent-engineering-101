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

REQUIRED_CONDITIONS = ("baseline", "homogeneous", "overconfident")  # check_week03.py's schema
CONDITIONS = [*REQUIRED_CONDITIONS, "reputation", "recovery"]
RUNS_PER_CONDITION = 3
BASE_SKILLS = {"A": "arithmetic", "B": "writing", "C": "coding"}
REPUTATION_CONDITIONS = ("reputation", "recovery")  # axis 1+3 extension, own Reputation object


def build_team(condition):
    """The three required conditions differ only here. "reputation" reuses
    the baseline team (control: does reputation change an already-working
    team?). "recovery" reuses the homogeneous team (does reputation fix a
    condition that fails without it?)."""
    if condition in ("baseline", "reputation"):
        return [Candidate(name=n, skill=s) for n, s in BASE_SKILLS.items()]
    if condition in ("homogeneous", "recovery"):
        return [Candidate(name=n, skill="general problem solving") for n in BASE_SKILLS]
    if condition == "overconfident":
        return [Candidate(name=n, skill=s, overconfident=(n == "C"))
                for n, s in BASE_SKILLS.items()]
    raise ValueError(f"unknown condition: {condition}")


def _open_results(path):
    """check_week03.py rejects any condition outside REQUIRED_CONDITIONS, so
    extension conditions (reputation, recovery, ...) go to a sibling file
    instead of breaking the required results.csv."""
    is_new = not os.path.exists(path)
    if is_new:
        run_number = 0
    else:
        with open(path, encoding="utf-8", newline="") as f:
            rows = [row for row in csv.reader(f) if row][1:]  # skip header
        run_number = max((int(row[0]) for row in rows if row[0].isdigit()), default=0)
    f = open(path, "a", newline="", encoding="utf-8")
    writer = csv.writer(f)
    if is_new:
        writer.writerow(["run", "condition", "tasks", "correct", "messages",
                          "unassigned", "misawards", "note"])
    return f, writer, run_number


def main(conditions):
    with open("tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)
    os.makedirs("logs", exist_ok=True)

    main_file, main_writer, main_run = _open_results("results.csv")
    ext_file, ext_writer, ext_run = _open_results("results_extension.csv")

    for condition in conditions:
        required = condition in REQUIRED_CONDITIONS
        writer, file_, run_number = (main_writer, main_file, main_run) if required \
            else (ext_writer, ext_file, ext_run)
        # one Reputation per condition: accumulates across its 3 reps, then
        # discarded -- never leaks into the next condition's comparison
        reputation = Reputation() if condition in REPUTATION_CONDITIONS else None
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
            file_.flush()

            stamp = datetime.now().strftime("%m%d-%H%M%S")
            with open(f"logs/{condition}-{rep}-{stamp}.txt", "w", encoding="utf-8") as lf:
                lf.write("\n".join(lines) + "\n")
        if required:
            main_run = run_number
        else:
            ext_run = run_number

    main_file.close()
    ext_file.close()


if __name__ == "__main__":
    main(sys.argv[1:] or CONDITIONS)
