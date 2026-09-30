"""Week 04 runner -- executes the three conditions and writes results.csv
and logs/ per weeks/week-04/README.md.

One log file per (condition, repeat), covering every scenario in that run,
as the README specifies. results.csv gets one row per episode (one
scenario within one run). Restarting after an interruption skips
(run, condition, scenario) rows already written, per the README's advice
for flaky free-tier providers -- harmless here too, just resumable.

Usage:
    python run_lab.py                       # 3 repeats per condition (default)
    python run_lab.py --repeats 3
    python run_lab.py --condition free --repeats 3
"""
import argparse
import csv
import sys
from pathlib import Path

from negotiation import MODEL, TEMPERATURE, Meter, load_scenarios, run_episode

# Windows consoles are often a non-UTF-8 codepage; agent replies can contain
# characters (em dashes, curly quotes) that codepage can't encode. Replace
# rather than crash -- the log FILES are still written as full-fidelity UTF-8.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
CONDITIONS = ("free", "tagged", "structured")
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]


def already_done(results_path: Path) -> set[tuple[str, str, str]]:
    if not results_path.exists():
        return set()
    with results_path.open(encoding="utf-8") as f:
        return {(row["run"], row["condition"], row["scenario"])
                for row in csv.DictReader(f)}


def run_one(condition: str, run_index: int, scenarios: list[dict],
            results_path: Path, log_dir: Path, done: set):
    log_lines = []

    def log(line: str):
        print(line)
        log_lines.append(line)

    log_name = f"{condition}-{run_index:02d}.txt"
    if all((str(run_index), condition, s["id"]) in done for s in scenarios):
        # Every scenario for this run is already in results.csv -- skip it
        # entirely so the existing log file (the actual transcript) is left
        # untouched instead of being overwritten with nothing but skip-lines.
        print(f"=== run {run_index} ({condition}) fully done, skipping ===")
        return
    log(f"=== negotiation run: condition={condition} run={run_index} "
        f"model={MODEL} temperature={TEMPERATURE or 'not settable (see REPORT.md)'} ===")

    rows = []
    for scenario in scenarios:
        key = (str(run_index), condition, scenario["id"])
        if key in done:
            log(f"--- scenario {scenario['id']}: already in results.csv, skipping ---")
            continue
        log(f"--- scenario {scenario['id']}: {scenario['item']} "
            f"(reserve={scenario['reserve']}, budget={scenario['budget']}) ---")
        meter = Meter()
        row = {"run": run_index, "condition": condition, "scenario": scenario["id"],
               "deal_possible": int(scenario["reserve"] <= scenario["budget"]),
               "outcome": "", "price": "", "correct": "", "violation": "",
               "turns": "", "format_errors": "", "reader_calls": "", "note": ""}
        try:
            result = run_episode(scenario, condition, meter, log)
            row.update(outcome=result["outcome"],
                        price=result["price"] if result["price"] is not None else "",
                        correct=result["correct"], violation=result["violation"],
                        turns=result["turns"], format_errors=result["format_errors"],
                        reader_calls=result["reader_calls"], note=result["note"])
            log(f"  [episode] outcome={result['outcome']} price={result['price']} "
                f"correct={result['correct']} violation={result['violation']} "
                f"tokens={meter.tokens} calls={meter.iters}")
        except Exception as e:  # crashed episode: keep the row, blank the counts, log the error
            row["note"] = f"crashed: {type(e).__name__}: {e}"
            log(f"  [episode] CRASHED: {type(e).__name__}: {e}")
        rows.append(row)

    (log_dir / log_name).write_text("\n".join(log_lines), encoding="utf-8")

    write_header = not results_path.exists()
    with results_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--condition", choices=CONDITIONS, default=None,
                     help="run only this condition (default: all three)")
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.json"))
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenarios)
    results_path = HERE / "results.csv"
    log_dir = HERE / "logs"
    log_dir.mkdir(exist_ok=True)

    conditions = [args.condition] if args.condition else list(CONDITIONS)
    run_index = 1
    for condition in conditions:
        for _ in range(args.repeats):
            done = already_done(results_path)
            run_one(condition, run_index, scenarios, results_path, log_dir, done)
            run_index += 1


if __name__ == "__main__":
    sys.exit(main())
