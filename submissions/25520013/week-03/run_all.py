"""Run the three conditions, three runs each, and write results.csv and logs/.

    uv run run_all.py                       # 3 conditions x 3 runs
    uv run run_all.py --runs 1 --conditions baseline     # a trial

Only the contractors' system prompts differ between conditions. The task set,
the announcement, the award rule, and the model are the same everywhere, which
is what makes the three columns comparable.
"""
import argparse
import csv
import json
import pathlib
import traceback

import backend
from contract_net import contractor, run_round

HERE = pathlib.Path(__file__).resolve().parent
CONDITIONS = ["baseline", "homogeneous", "overconfident"]
FIELDS = ["run", "condition", "tasks", "correct", "messages", "unassigned",
          "misawards", "note"]

SKILLS = {
    "A": "arithmetic — you evaluate numeric expressions and return the number",
    "B": "plain-English writing — you rewrite and summarise text for people",
    "C": "Python — you write small, correct functions",
}
GENERALIST = "general problem solving — you can handle any kind of task"


def team_for(condition):
    """The only thing a condition changes is the three system prompts."""
    if condition == "homogeneous":
        return [contractor(name, GENERALIST) for name in "ABC"]
    team = [contractor(name, SKILLS[name]) for name in "ABC"]
    if condition == "overconfident":
        team[0] = contractor("A", SKILLS["A"], overconfident=True)
    return team


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    args = ap.parse_args()

    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    (HERE / "logs").mkdir(exist_ok=True)

    with open(HERE / "results.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for condition in args.conditions:
            for run in range(1, args.runs + 1):
                row = one_run(condition, run, tasks)
                writer.writerow(row)
                fh.flush()          # a crash later must not cost the rows before it
                print(f"{condition} run {run}: {row['note']}")


def one_run(condition, run, tasks):
    """One run: its own log file, its own row. A crash keeps both."""
    path = HERE / "logs" / f"{condition}-run{run}.txt"
    meter = backend.Meter()
    with open(path, "w", encoding="utf-8") as log_file:
        def log(line):
            print(line, file=log_file, flush=True)

        log(f"condition={condition} run={run} model={backend.MODEL} "
            f"tasks={len(tasks)}")
        try:
            metrics = run_round(tasks, team_for(condition), meter, log)
        except Exception:
            log("\n" + traceback.format_exc())
            return {"run": run, "condition": condition, "tasks": "",
                    "correct": "", "messages": "", "unassigned": "",
                    "misawards": "",
                    "note": f"crashed: {traceback.format_exc(0).strip()}"
                            .replace(",", ";").replace("\n", " ")}
        note = (f"model={backend.MODEL} calls={meter.calls} "
                f"tokens={meter.tokens} parse_fails={metrics['parse_fails']} "
                f"cli_failures={meter.failures}")
        log("\n" + note)
        return {"run": run, "condition": condition, "tasks": metrics["tasks"],
                "correct": metrics["correct"], "messages": metrics["messages"],
                "unassigned": metrics["unassigned"],
                "misawards": metrics["misawards"], "note": note}


if __name__ == "__main__":
    main()
