"""Week 03 — run.py: build a team per condition, run 3x each, log everything.

Usage: python run.py
Needs ANTHROPIC_API_KEY (or OPENAI_API_KEY + OPENAI_BASE_URL) in the
environment. Appends to results.csv and writes one log file per run under
logs/ (never overwrites -- crashed and old runs stay as evidence).
"""
import csv
import json
import os
from datetime import datetime

from contractor import Contractor
from manager import run_round
from model import MODEL, PROVIDER, TEMPERATURE, Meter

CONDITIONS = ["baseline", "homogeneous", "overconfident"]
RUNS_PER_CONDITION = 3
BASE_SKILLS = {"A": "arithmetic", "B": "writing", "C": "coding"}


def build_team(condition):
    """The one place the three conditions differ. Everything else -- tasks,
    prompts, model, temperature -- stays fixed across all of them."""
    if condition == "baseline":
        return [Contractor(name=n, skill=s) for n, s in BASE_SKILLS.items()]
    if condition == "homogeneous":
        return [Contractor(name=n, skill="general problem solving") for n in BASE_SKILLS]
    if condition == "overconfident":
        return [Contractor(name=n, skill=s, overconfident=(n == "C"))
                for n, s in BASE_SKILLS.items()]
    raise ValueError(f"unknown condition: {condition}")


def main():
    with open("tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)

    os.makedirs("logs", exist_ok=True)
    is_new = not os.path.exists("results.csv")
    csv_file = open("results.csv", "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    if is_new:
        writer.writerow(["run", "condition", "tasks", "correct", "messages",
                          "unassigned", "misawards", "note"])

    run_number = 0
    for condition in CONDITIONS:
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
                result = run_round(tasks, team, meter, log=log)
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
    main()
