"""Week 03 — Contract Net 실험 실행기.

Usage:
  python run_experiment.py --condition baseline --runs 3
  python run_experiment.py --condition homogeneous --runs 3
  python run_experiment.py --condition overconfident --runs 3
"""
import argparse
import csv
import json
import os
import time
from pathlib import Path

from contractor import Contractor, Meter, MODEL
from manager import run_round

# ---- 세 가지 조건 ----

def make_team(condition):
    if condition == "baseline":
        return [
            Contractor("A", "computation and arithmetic"),
            Contractor("B", "writing and text editing"),
            Contractor("C", "coding and programming"),
        ]
    elif condition == "homogeneous":
        return [
            Contractor("A", "general problem solving"),
            Contractor("B", "general problem solving"),
            Contractor("C", "general problem solving"),
        ]
    elif condition == "overconfident":
        return [
            Contractor("A", "computation and arithmetic"),
            Contractor("B", "writing and text editing"),
            Contractor("C", "coding and programming", overconfident=True),
        ]
    else:
        raise ValueError(f"unknown condition: {condition}")


HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True,
                    choices=["baseline", "homogeneous", "overconfident"])
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tasks = json.loads(Path("tasks.json").read_text(encoding="utf-8"))
    Path("logs").mkdir(exist_ok=True)

    new_file = not Path("results.csv").exists()
    run_no = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            run_no = sum(1 for _ in f) - 1

    provider = f"OpenRouter ({os.environ.get('OPENAI_BASE_URL', 'default')})"

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)

        for _ in range(args.runs):
            run_no += 1
            meter = Meter()
            team = make_team(args.condition)
            lines = []

            def log(msg, _lines=lines):
                print(msg)
                _lines.append(str(msg))

            # 로그 첫 줄: provider, 모델명, temperature
            log(f"provider: {provider}, model: {MODEL}, temperature: default (not configurable on free tier)")

            t0 = time.time()
            note = ""
            try:
                result = run_round(tasks, team, meter, log)
                if result.parse_fails > 0:
                    note = f"parse_fails={result.parse_fails}"
            except Exception as e:
                result = None
                note = f"crash: {type(e).__name__}: {e}"
                log(note)

            elapsed = time.time() - t0

            if result:
                log(f"\n[summary] correct={result.correct}/{result.tasks} "
                    f"messages={result.messages} unassigned={result.unassigned} "
                    f"misawards={result.misawards} parse_fails={result.parse_fails} "
                    f"({elapsed:.1f}s)")
                w.writerow([run_no, args.condition, result.tasks,
                            result.correct, result.messages,
                            result.unassigned, result.misawards, note])
            else:
                log(f"\n[summary] crashed ({elapsed:.1f}s)")
                w.writerow([run_no, args.condition, len(tasks),
                            "", "", "", "", note])

            f.flush()
            Path("logs", f"{args.condition}-{run_no:02d}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            print(f"--- run {run_no} done ---\n")

    print("results.csv updated")


if __name__ == "__main__":
    main()
