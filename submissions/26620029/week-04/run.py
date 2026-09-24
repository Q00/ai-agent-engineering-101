"""Runner: three conditions x three repeats x all scenarios.

Writes one row per episode to results.csv and one log file per (condition,
repeat) run to logs/. Safe to re-run: already-completed (run, scenario)
pairs found in results.csv are skipped, so an interrupted run continues
where it stopped.
"""
import csv
import json
import sys
import traceback
from pathlib import Path

from model import MODEL, TEMPERATURE
from negotiation import run_episode

HERE = Path(__file__).resolve().parent
SCENARIOS_PATH = HERE / "scenarios.json"
RESULTS_PATH = HERE / "results.csv"
LOGS_DIR = HERE / "logs"

CONDITIONS = ("free", "tagged", "structured")
REPEATS = 5
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]


def load_scenarios():
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def load_done_pairs():
    if not RESULTS_PATH.is_file():
        return set()
    with RESULTS_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEADER:
        return set()
    return {(r[0], r[2]) for r in rows[1:] if len(r) == len(HEADER)}


def append_result(row: dict):
    is_new = not RESULTS_PATH.is_file()
    with RESULTS_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(HEADER)
        writer.writerow([row.get(k, "") if row.get(k, "") is not None else "" for k in HEADER])


def main():
    LOGS_DIR.mkdir(exist_ok=True)
    scenarios = load_scenarios()
    done = load_done_pairs()

    for condition in CONDITIONS:
        for repeat in range(1, REPEATS + 1):
            run_id = f"{condition}-r{repeat}"
            log_path = LOGS_DIR / f"{run_id}.txt"
            log_lines = []
            if log_path.is_file():
                log_lines.append(log_path.read_text(encoding="utf-8"))

            def log(msg, _lines=log_lines):
                print(f"[{run_id}] {msg}")
                _lines.append(msg + "\n")

            wrote_any = False
            for scenario in scenarios:
                scenario_id = str(scenario["id"])
                if (run_id, scenario_id) in done:
                    print(f"[{run_id}] scenario {scenario_id}: already in results.csv, skipping")
                    continue
                try:
                    result = run_episode(scenario, condition, log=log)
                    result["run"] = run_id
                    result["condition"] = condition
                except Exception as e:
                    traceback.print_exc()
                    log(f"CRASH on scenario {scenario_id}: {e}")
                    result = {"run": run_id, "condition": condition, "scenario": scenario_id,
                               "deal_possible": "", "outcome": "", "price": "", "correct": "",
                               "violation": "", "turns": "", "format_errors": "",
                               "reader_calls": "", "note": f"crashed: {e}"}
                append_result(result)
                wrote_any = True

            if wrote_any or not log_path.is_file():
                header = (f"model={MODEL} temperature={TEMPERATURE} condition={condition} "
                          f"repeat={repeat}\n\n")
                log_path.write_text(header + "".join(log_lines), encoding="utf-8")

    print("done")


if __name__ == "__main__":
    sys.exit(main())
