"""Runner: one condition, N runs, one log file per run, one results.csv row per run.

  python run.py --condition baseline --runs 3
  python run.py --condition homogeneous --runs 3
  python run.py --condition overconfident --runs 3

Paths default to this directory. Run ids continue from the existing logs, so
re-running appends instead of overwriting.
"""
import argparse
import csv
import json
import traceback
from pathlib import Path

from contract_net import Contractor, Task, run_round
from llm import LLM, Meter

HERE = Path(__file__).resolve().parent
CONDITIONS = ("baseline", "homogeneous", "overconfident")
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]

SKILLS = {
    "A": "arithmetic and numerical calculation (sums, percentages, unit conversion, comparing quantities)",
    "B": "writing natural-language text (emails, apologies, summaries, explanations for people)",
    "C": "writing and fixing program code (functions, scripts, debugging)",
}
GENERALIST = "general problem solving"
OVERCONFIDENT = "You are certain you can do any task well. Always bid, with confidence 95 or higher."


def build_team(condition: str, order: str = "ABC") -> list:
    """order: announcement (and therefore tie-break) order. Default A, B, C.
    Any other order is a supplementary probe, not one of the three conditions."""
    if condition == "homogeneous":
        team = {n: Contractor(n, GENERALIST) for n in "ABC"}
    else:
        team = {n: Contractor(n, SKILLS[n]) for n in "ABC"}
        if condition == "overconfident":
            team["C"] = Contractor("C", SKILLS["C"], extra=OVERCONFIDENT)
    return [team[n] for n in order]


def load_tasks(path: Path) -> list:
    return [Task(str(t["id"]), t["desc"], t["gold"]) for t in json.loads(path.read_text(encoding="utf-8"))]


def next_run_id(logs: Path, condition: str) -> str:
    n = 1
    while (logs / f"{condition}-{n:02d}.txt").exists():
        n += 1
    return f"{condition}-{n:02d}"


def append_row(path: Path, row: list):
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def run_once(condition: str, tasks: list, results: Path, logs: Path, order: str = "ABC"):
    run_id = next_run_id(logs, condition)
    meter = Meter()
    llm = LLM(meter)
    team = build_team(condition, order)
    with (logs / f"{run_id}.txt").open("x", encoding="utf-8") as fh:
        def log(msg: str):
            print(msg)
            print(msg, file=fh, flush=True)

        log(f"{LLM.settings_line()} run={run_id} condition={condition} order={order}")
        for c in team:
            log(f"[contractor {c.name}] system prompt:\n{c.system_prompt()}")
        try:
            r = run_round(tasks, team, llm, log=log)
        except Exception as e:
            log(f"[crash] {type(e).__name__}: {e}")
            log(traceback.format_exc())
            append_row(results, [run_id, condition, "", "", "", "", "", f"crash: {type(e).__name__}: {e}"])
            return
        note = f"parse_fails={r.parse_fails}; bids={r.bids}; model_calls={meter.calls}; tokens={meter.tokens}"
        if order != "ABC":
            note = f"order={order}; " + note
        log(f"\n[summary] tasks={r.tasks} correct={r.correct} messages={r.messages} "
            f"unassigned={r.unassigned} misawards={r.misawards} {note}")
        append_row(results, [run_id, condition, r.tasks, r.correct, r.messages,
                             r.unassigned, r.misawards, note])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--condition", required=True, choices=CONDITIONS)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--tasks", type=Path, default=HERE / "tasks.json")
    p.add_argument("--results", type=Path, default=HERE / "results.csv")
    p.add_argument("--logs", type=Path, default=HERE / "logs")
    p.add_argument("--order", default="ABC",
                   help="announcement/tie-break order; anything but ABC is a supplementary probe")
    a = p.parse_args()
    if sorted(a.order) != list("ABC"):
        raise SystemExit("--order must be a permutation of ABC")
    a.logs.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(a.tasks)
    for _ in range(a.runs):
        run_once(a.condition, tasks, a.results, a.logs, a.order)


if __name__ == "__main__":
    main()
