"""Week 04 — run free/tagged/structured conditions and record results.csv.

Usage: python run_experiment.py [--repeats 3] [--scenarios 1]

One "run" = one condition, one repeat, all scenarios in scenarios.json.
run numbers are deterministic (condition index * repeats + repeat), so a
resume recomputes the same run number for the same (condition, repeat) and
only the (run, scenario) pairs missing from results.csv are executed --
already-recorded episodes are never repeated. Each run's log file
accumulates across resumes instead of being overwritten.
"""
import argparse
import csv
import json
from pathlib import Path

from episode import run_episode, MAX_TURNS
from tools_shared import MODEL, TEMPERATURE

HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ["free", "tagged", "structured"]


def load_done_pairs(results_path: Path):
    """Return {(run_label, scenario_id)} already present in results.csv."""
    if not results_path.exists():
        return set()
    with open(results_path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEADER:
        return set()
    return {(r[0], r[2]) for r in rows[1:] if len(r) == len(HEADER)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--scenarios", type=int, default=None,
                     help="only run the first N scenarios (smoke testing)")
    args = ap.parse_args()

    scenarios = json.load(open("scenarios.json", encoding="utf-8"))
    if args.scenarios:
        scenarios = scenarios[:args.scenarios]
    Path("logs").mkdir(exist_ok=True)
    results_path = Path("results.csv")
    done = load_done_pairs(results_path)
    new_file = not results_path.exists()

    with open(results_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for condition in CONDITIONS:
            for repeat in range(1, args.repeats + 1):
                run_label = f"{condition}-{repeat:02d}"
                run_no = run_label  # stable identifier, independent of --repeats
                log_path = Path("logs", f"{run_label}.txt")
                lines = []
                if log_path.exists():
                    lines.append(log_path.read_text(encoding="utf-8").rstrip("\n"))
                else:
                    lines.append(f"provider=Anthropic model={MODEL} "
                                 f"temperature={TEMPERATURE} max_turns={MAX_TURNS}")

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                ran_any = False
                for s in scenarios:
                    if (str(run_no), s["id"]) in done:
                        continue
                    ran_any = True
                    log(f"=== scenario {s['id']} ({s['item']}) "
                        f"reserve={s['reserve']} budget={s['budget']} ===")
                    try:
                        r = run_episode(s, condition, log=log)
                        row = [run_no, condition, r.scenario, r.deal_possible, r.outcome,
                               r.price if r.price is not None else "", r.correct, r.violation,
                               r.turns, r.format_errors, r.reader_calls, r.note]
                    except Exception as e:                # a crash is a failed episode, not a lost one
                        note = f"crash: {type(e).__name__}: {e}"
                        log(f"[crash] {note}")
                        row = [run_no, condition, s["id"], "", "", "", "", "", "", "", "", note]
                    w.writerow(row)
                    f.flush()

                if ran_any:
                    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nresults.csv updated;", results_path.resolve())


if __name__ == "__main__":
    main()
