import os
import json
import csv
import time
from dataclasses import dataclass
from pathlib import Path
from contractor import Contractor, setup_team, bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def run_round(tasks: list, team: list[Contractor], meter=None, log=print) -> RoundResult:
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        cid = t["id"]
        desc = t["desc"]
        gold = t["gold"]
        cost = t["cost"]

        log(f"\n[Task {cid}] {desc} (Gold: {gold}, Cost: {cost})")
        log(f"  Current Budgets -> A: {team[0].budget}, B: {team[1].budget}, C: {team[2].budget}")

        r.messages += len(team)  # broadcast announcement
        bids = []

        for c in team:
            time.sleep(0.3)
            # Budget check before letting them bid
            if c.budget < cost:
                log(f"  [bid] {c.name}: SKIPPED (Insufficient budget: {c.budget} < {cost})")
                continue

            b = bid(c, cid, desc, cost, meter)
            if b is None:
                r.parse_fails += 1
                log(f"  [bid] {c.name}: PARSE_FAIL")
                continue

            log(f"  [bid] {c.name}: bid={b.get('bid')} confidence={b.get('confidence')} reason={b.get('reason')}")
            if b.get("bid") is True:
                # Double check budget validity of bid
                if c.budget < cost:
                    log(f"    -> Ignored bid: insufficient budget ({c.budget} < {cost})")
                    continue
                r.messages += 1
                bids.append((b.get("confidence", 0), c))

        if not bids:
            r.unassigned += 1
            log(f"  -> [result] UNASSIGNED (No valid bids or budget exhaustion)")
            continue

        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1  # award message

        # Deduct budget
        winner.budget -= cost
        log(f"  -> [award] Team {winner.name} (Gold: {gold}). New Budget: {winner.budget}")

        if winner.name == gold:
            r.correct += 1
        else:
            r.misawards += 1

    return r


def main():
    base_dir = Path(__file__).resolve().parent
    tasks_path = base_dir / "tasks.json"
    logs_dir = base_dir / "logs"
    results_path = base_dir / "results.csv"

    logs_dir.mkdir(exist_ok=True)

    with open(tasks_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    conditions = ["baseline", "homogeneous", "overconfident"]
    runs_per_condition = 3

    csv_rows = []
    run_id = 1

    provider = os.getenv("PROVIDER", "Google AI Studio (OpenAI Compatible)")
    model_name = os.getenv("AGENT_MODEL", "gemini-3.5-flash-lite")
    temperature = 0.2

    print(f"Starting KBO Draft Contract Net Experiment (Total 9 runs)...")

    for condition in conditions:
        for _ in range(runs_per_condition):
            log_file_path = logs_dir / f"run-{run_id:02d}.log"
            team = setup_team(condition)
            note = ""

            with open(log_file_path, "w", encoding="utf-8") as lf:
                lf.write(f"provider: {provider}\n")
                lf.write(f"model: {model_name}\n")
                lf.write(f"temperature: {temperature}\n")
                lf.write(f"condition: {condition}\n")
                lf.write(f"run: {run_id}\n")
                lf.write("=" * 50 + "\n")

                def log_both(msg: str):
                    print(msg)
                    lf.write(msg + "\n")

                log_both(f"\n--- Run {run_id} | Condition: {condition} ---")

                try:
                    result = run_round(tasks, team, meter=None, log=log_both)

                    if result.parse_fails > 0:
                        note = f"parse_fails={result.parse_fails}"

                    csv_rows.append({
                        "run": run_id,
                        "condition": condition,
                        "tasks": result.tasks,
                        "correct": result.correct,
                        "messages": result.messages,
                        "unassigned": result.unassigned,
                        "misawards": result.misawards,
                        "note": note
                    })
                except Exception as e:
                    log_both(f"CRASH: {str(e)}")
                    csv_rows.append({
                        "run": run_id,
                        "condition": condition,
                        "tasks": len(tasks),
                        "correct": "",
                        "messages": "",
                        "unassigned": "",
                        "misawards": "",
                        "note": f"crash: {str(e)}"
                    })

            run_id += 1

    fieldnames = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nAll KBO draft experiments finished. Results saved to {results_path} and {logs_dir}/")


if __name__ == "__main__":
    main()
