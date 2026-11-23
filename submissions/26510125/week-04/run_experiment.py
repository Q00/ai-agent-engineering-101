"""Week 04 — run the format A/B/C experiment and record results.csv.

Usage: python run_experiment.py [--repeats 3] [--turn-limit 8] [--conditions free tagged structured]

One run = one condition, one repeat, all scenarios; one log file per run.
The runner appends rows and skips (run, scenario) pairs already present in
results.csv, so an interrupted run continues where it stopped. A crashed
episode stays as a row with blank fields and the error in note.
"""
import argparse
import csv
import json
import time
from pathlib import Path

from negotiation import MODEL, PROVIDER, TEMPERATURE, run_episode

HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ["free", "tagged", "structured"]


def done_pairs(csv_path: Path) -> set:
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--turn-limit", type=int, default=8)
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    args = ap.parse_args()

    scenarios = json.loads(Path("scenarios.json").read_text(encoding="utf-8"))
    Path("logs").mkdir(exist_ok=True)
    csv_path = Path("results.csv")
    is_new = not csv_path.exists()
    done = done_pairs(csv_path)
    print(f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE} turn_limit={args.turn_limit}")

    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(HEADER)
        for condition in args.conditions:
            for rep in range(1, args.repeats + 1):
                run_id = f"{condition}-r{rep}"
                log_path = Path("logs", f"{run_id}.txt")
                captured = []

                def log(msg, _c=captured):
                    print(msg)
                    _c.append(str(msg))

                log(f"=== run {run_id} · provider={PROVIDER} model={MODEL} "
                    f"temperature={TEMPERATURE} turn_limit={args.turn_limit} ===")
                for sc in scenarios:
                    if (run_id, str(sc["id"])) in done:
                        log(f"[skip] {sc['id']} already in results.csv")
                        continue
                    t0 = time.time()
                    try:
                        m = run_episode(condition, sc, turn_limit=args.turn_limit, log=log)
                        row = [run_id, condition, sc["id"], m["deal_possible"], m["outcome"],
                               "" if m["price"] is None else m["price"], m["correct"],
                               m["violation"], m["turns"], m["format_errors"], m["reader_calls"], ""]
                    except Exception as e:
                        note = f"crash: {type(e).__name__}: {str(e)[:200]}"
                        log(f"[crash] {sc['id']}: {note}")
                        row = [run_id, condition, sc["id"], int(sc["reserve"] <= sc["budget"]),
                               "", "", "", "", "", "", "", note]
                    log(f"[time] {time.time() - t0:.1f}s")
                    writer.writerow(row)
                    f.flush()
                    done.add((run_id, str(sc["id"])))

                # append to the run's log so a resumed run keeps earlier captures
                with log_path.open("a", encoding="utf-8") as lf:
                    lf.write("\n".join(captured) + "\n")

    print("\nresults.csv updated:", csv_path.resolve())


if __name__ == "__main__":
    main()
