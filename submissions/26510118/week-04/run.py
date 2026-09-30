"""The runner: 3 conditions x 3 repeats x every scenario, appended and resumable.

A `run` is one condition and one repeat over every scenario, so there are nine
of them and nine log files. Run numbers are positional, not a counter, so a
resumed run recomputes the same number and the skip list works:

    run 1-3  free        run 4-6  tagged        run 7-9  structured

Usage:
    python run.py                       # everything still missing
    python run.py --only free,tagged    # just those conditions
"""
import csv
import hashlib
import json
import os
import sys
import traceback

import model
import negotiate

CONDITIONS = ("free", "tagged", "structured")
REPEATS = 3
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
RESULTS = "results.csv"
LOGDIR = "logs"

# run number -> (condition, repeat). Position decides it, so resuming is stable.
PLAN = [(c, r) for c in CONDITIONS for r in range(1, REPEATS + 1)]


def load_scenarios():
    raw = open("scenarios.json", encoding="utf-8").read()
    # Fingerprint of the scenario file. Rows from different scenario sets must
    # never be averaged together; this is how you tell them apart later.
    return json.loads(raw), hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def done_pairs():
    """(run, scenario) pairs already in results.csv, so an interrupted run resumes."""
    if not os.path.exists(RESULTS):
        return set()
    with open(RESULTS, newline="", encoding="utf-8") as f:
        return {(r.get("run", "").strip(), r.get("scenario", "").strip())
                for r in csv.DictReader(f)}


def append_row(row):
    new = not os.path.exists(RESULTS)
    with open(RESULTS, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if new:
            w.writeheader()
        w.writerow(row)


def main(argv):
    only = None
    for a in argv:
        if a.startswith("--only"):
            only = set(a.split("=", 1)[1].split(",")) if "=" in a else None
    if only is None and "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))

    scenarios, fingerprint = load_scenarios()
    os.makedirs(LOGDIR, exist_ok=True)
    done = done_pairs()
    header = (f"{model.settings_line()} max_turns={negotiate.MAX_TURNS} "
              f"scenarios={fingerprint}")
    print(header)

    for i, (condition, repeat) in enumerate(PLAN):
        run_no = i + 1
        if only and condition not in only:
            continue
        todo = [s for s in scenarios if (str(run_no), str(s["id"])) not in done]
        if not todo:
            print(f"[run {run_no}] {condition} repeat {repeat}: already complete, skipping")
            continue

        path = os.path.join(LOGDIR, f"{condition}-{repeat}.txt")
        with open(path, "a", encoding="utf-8") as fh:
            def log(*parts):
                line = " ".join(str(p) for p in parts)
                print(line)
                fh.write(line + "\n")
                fh.flush()

            log(header)
            log(f"run={run_no} condition={condition} repeat={repeat} "
                f"scenarios={len(todo)}/{len(scenarios)}")
            for sc in todo:
                try:
                    ep = negotiate.run_episode(sc, condition, log=log)
                    row = ep.row(run_no)
                    row["note"] = f"{row['note']} scenarios={fingerprint}"
                except Exception:                       # noqa: BLE001
                    # The episode stays in the table with blank fields and the
                    # reason in note. Deleting it would hide a failure.
                    log(traceback.format_exc())
                    row = {k: "" for k in HEADER}
                    row.update(run=run_no, condition=condition, scenario=sc["id"],
                               deal_possible=int(sc["reserve"] <= sc["budget"]),
                               note=f"crash: {sys.exc_info()[1]!r} scenarios={fingerprint}")
                append_row(row)
                log("")


if __name__ == "__main__":
    main(sys.argv[1:])
