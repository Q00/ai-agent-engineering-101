"""Week 04 — run the buyer/seller negotiation experiment and record results.csv.

Usage: python run_experiment.py [--repeats 3]

Loads scenarios.json, runs each of the three conditions --repeats times over
every scenario, appends one results.csv row per episode, and saves one
console capture per (condition, repeat) run under logs/ -- every message,
every reader label or parse result, and the episode outcome, for all
scenarios in that run. A crashed episode stays in results.csv with blank
fields and the error in `note`, per the README: crashed episodes are
grading evidence, not something to silently drop.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

from negotiation import CONDITIONS, run_episode

HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]


def load_scenarios(path="scenarios.json"):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--start-repeat", type=int, default=1,
                     help="first repeat number to run (for chunking a long experiment "
                          "across several invocations; results.csv is appended, not "
                          "overwritten, so this is safe to call multiple times)")
    ap.add_argument("--condition", choices=CONDITIONS, default=None,
                     help="run only this condition instead of all three (for chunking)")
    args = ap.parse_args()
    conditions = [args.condition] if args.condition else list(CONDITIONS)

    scenarios = load_scenarios()
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
        for condition in conditions:
            for repeat in range(args.start_repeat, args.start_repeat + args.repeats):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                log(f"=== run {run_no}: condition={condition} repeat={repeat} "
                    f"({len(scenarios)} scenarios) ===")
                t0 = time.time()

                for scenario in scenarios:
                    log(f"\n--- scenario {scenario['id']} ({scenario['item']}, "
                        f"reserve={scenario['reserve']}, budget={scenario['budget']}) ---")
                    note = ""
                    result = None
                    try:
                        result = run_episode(scenario, condition, log)
                        note = f"agent_calls={result['agent_calls']}"
                    except Exception as e:  # a crash is a failed episode, not a lost one
                        note = f"crash: {type(e).__name__}: {e}"
                        log(note)

                    if result is None:
                        w.writerow([run_no, condition, scenario["id"], "", "", "", "",
                                    "", "", "", "", note])
                    else:
                        w.writerow([run_no, condition, scenario["id"],
                                    result["deal_possible"], result["outcome"],
                                    result["price"] if result["price"] is not None else "",
                                    result["correct"], result["violation"], result["turns"],
                                    result["format_errors"], result["reader_calls"], note])
                    f.flush()

                log(f"\n[done] run {run_no} in {time.time() - t0:.1f}s")
                Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
    print("\nresults.csv updated;", Path("results.csv").resolve())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp949
    main()
