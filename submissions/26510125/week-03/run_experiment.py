"""Week 03 — run the contract-net experiment and record results.csv.

Usage: python run_experiment.py [--runs 3]

Loads tasks.json, runs each condition --runs times, appends one row per run
to results.csv, and saves each run's console output under logs/. A crashed
run is kept with blank counts and the error in note — it stays, it's not
discarded.
"""
import argparse
import csv
import json
import time
from pathlib import Path

from contract_net import run_round

CSV_HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
CONDITIONS = ["baseline", "homogeneous", "overconfident"]


def load_tasks(path: str = "tasks.json") -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def next_run_number(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with csv_path.open(encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    tasks = load_tasks()
    Path("logs").mkdir(exist_ok=True)
    csv_path = Path("results.csv")
    is_new = not csv_path.exists()
    run_no = next_run_number(csv_path)

    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(CSV_HEADER)
        for condition in CONDITIONS:
            for _ in range(args.runs):
                run_no += 1
                captured = []

                def log(msg, _captured=captured):
                    print(msg)
                    _captured.append(str(msg))

                t0 = time.time()
                note = ""
                try:
                    result = run_round(condition, tasks, log=log)
                    row = [run_no, condition, result["tasks"], result["correct"],
                           result["messages"], result["unassigned"], result["misawards"], ""]
                except Exception as e:
                    note = f"crash: {type(e).__name__}: {e}"
                    log(note)
                    row = [run_no, condition, "", "", "", "", "", note]

                elapsed = time.time() - t0
                log(f"[run {run_no}] condition={condition} done in {elapsed:.1f}s")
                Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
                    "\n".join(captured) + "\n", encoding="utf-8")
                writer.writerow(row)
                f.flush()

    print("\nresults.csv updated:", csv_path.resolve())


if __name__ == "__main__":
    main()
