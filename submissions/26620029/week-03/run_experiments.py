"""Runner: three conditions x N runs each (default 3), same task set.

Default (no --update-root): every invocation is one experiment attempt and
gets its own folder under runs/<timestamp>[-label]/, holding that attempt's
results.csv, bids.csv, logs/, and a config.json snapshot (model, provider,
temperature, label, task-file hash) -- so repeated design changes (e.g. a
different ANNOUNCE_TEMPLATE, a different award rule) stay side by side
instead of overwriting each other.

--update-root reproduces the original week-03 behaviour: write results.csv
and logs/ directly under this directory, which is what CI and the grader
read (scripts/check_week03.py). Use it only to regenerate the graded
artifacts (it refuses --conditions other than exactly the three graded
ones); use the default for further experiments.

--conditions lets a one-off experiment run a subset of CONDITIONS (e.g.
the exploratory homogeneous_shuffled) without touching the default three.

Usage:
    python run_experiments.py [--runs N] [--label TEXT] [--update-root]
                               [--conditions c1,c2,...]
"""
import argparse
import csv
import hashlib
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from contract_net import CONDITIONS, GRADED_CONDITIONS, run_condition
import model

HERE = Path(__file__).parent
RESULTS_HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
BIDS_HEADER = ["run", "condition", "task_id", "contractor", "announce_position", "bid",
               "confidence", "input_tokens", "output_tokens", "total_tokens"]


def resolve_out_dir(label: str | None, update_root: bool) -> Path:
    if update_root:
        return HERE
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    name = f"{stamp}-{label}" if label else stamp
    out_dir = HERE / "runs" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="runs per condition")
    parser.add_argument("--label", default=None,
                         help="short tag for this attempt's runs/ folder, "
                              "e.g. --label blind-gold-v2")
    parser.add_argument("--update-root", action="store_true",
                         help="write results.csv/logs/ at the submission root "
                              "instead of a new runs/ folder (reproduces the "
                              "graded artifacts; do not use for new experiments)")
    parser.add_argument("--conditions", default=",".join(GRADED_CONDITIONS),
                         help="comma-separated condition names to run "
                              f"(default: {','.join(GRADED_CONDITIONS)})")
    args = parser.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    unknown = [c for c in conditions if c not in CONDITIONS]
    if unknown:
        parser.error(f"unknown condition(s): {', '.join(unknown)} "
                     f"(known: {', '.join(CONDITIONS)})")
    if args.update_root and tuple(conditions) != GRADED_CONDITIONS:
        parser.error("--update-root regenerates the graded root artifacts and only "
                      f"accepts exactly {','.join(GRADED_CONDITIONS)} -- omit "
                      "--conditions or drop --update-root for other experiments")

    tasks_path = HERE / "tasks.json"
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    tasks_hash = hashlib.sha256(tasks_path.read_bytes()).hexdigest()[:12]

    out_dir = resolve_out_dir(args.label, args.update_root)
    (out_dir / "logs").mkdir(exist_ok=True)

    if not args.update_root:
        (out_dir / "config.json").write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "label": args.label,
            "runs_per_condition": args.runs,
            "provider": model.PROVIDER,
            "model": model.MODEL,
            "temperature": model.TEMPERATURE,
            "tasks_json_sha256_12": tasks_hash,
        }, indent=2) + "\n", encoding="utf-8")

    rows = []
    bid_rows = []
    run_number = 0
    for condition in conditions:
        for i in range(1, args.runs + 1):
            run_number += 1
            log_path = out_dir / "logs" / f"{condition}-run{i}.txt"
            lines = [f"=== run {run_number} ({condition}), {datetime.now().isoformat()} ==="]

            def log(msg, _lines=lines):
                _lines.append(msg)
                print(msg)

            try:
                totals, bid_records = run_condition(condition, tasks, log)
                log(f"=== summary: tasks={totals['tasks']} correct={totals['correct']} "
                    f"messages={totals['messages']} unassigned={totals['unassigned']} "
                    f"misawards={totals['misawards']} ===")
                rows.append([run_number, condition, totals["tasks"], totals["correct"],
                             totals["messages"], totals["unassigned"], totals["misawards"], ""])
                for b in bid_records:
                    bid_rows.append([run_number, condition, b["task_id"], b["contractor"],
                                      b["announce_position"], b["bid"], b["confidence"],
                                      b["input_tokens"], b["output_tokens"], b["total_tokens"]])
            except Exception as e:
                log(f"=== CRASHED: {e} ===")
                lines.append(traceback.format_exc())
                rows.append([run_number, condition, "", "", "", "", "", f"crashed: {e}"])

            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    results_path = out_dir / "results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(RESULTS_HEADER)
        writer.writerows(rows)

    if not args.update_root:
        bids_path = out_dir / "bids.csv"
        with bids_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(BIDS_HEADER)
            writer.writerows(bid_rows)
        print(f"\nwrote {len(rows)} run(s) to {results_path}")
        print(f"wrote {len(bid_rows)} bid(s) with token cost to {bids_path}")
    else:
        print(f"\nwrote {len(rows)} run(s) to {results_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
