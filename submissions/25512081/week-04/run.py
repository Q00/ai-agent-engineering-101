"""Runner for the week-04 negotiation lab.

One run = one condition, one repeat, over every scenario. Each episode is one
row in results.csv; each run is one file in logs/. results.csv is appended and
(run, scenario) pairs already present are skipped, so an interrupted run
continues where it stopped.

    python run.py --repeats 3                 # needs an API key
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

import llm
import negotiate

HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ["free", "tagged", "structured"]


def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 3 and row[0].strip() and row[0] != "run":
                    done.add((row[0].strip(), row[2].strip()))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    ap.add_argument("--scenarios", default="scenarios.json")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--logs", default="logs")
    args = ap.parse_args()

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print("no API key in environment (ANTHROPIC_API_KEY / OPENAI_API_KEY).")
        return 1

    with open(args.scenarios, encoding="utf-8") as f:
        scenarios = json.load(f)
    os.makedirs(args.logs, exist_ok=True)

    done = load_done(args.out)
    new_file = not os.path.exists(args.out)
    out = open(args.out, "a", encoding="utf-8", newline="")
    writer = csv.writer(out)
    if new_file:
        writer.writerow(HEADER)

    print(f"provider={llm.PROVIDER} model={llm.MODEL} temp={llm.TEMPERATURE} "
          f"max_turns={negotiate.MAX_TURNS} repeats={args.repeats}\n")

    for condition in args.conditions:
        for rep in range(1, args.repeats + 1):
            run_id = f"{condition}-{rep}"
            lines = []

            def log(msg=""):
                print(msg)
                lines.append(str(msg))

            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log(f"# run={run_id} provider={llm.PROVIDER} model={llm.MODEL} "
                f"temp={llm.TEMPERATURE} max_turns={negotiate.MAX_TURNS} time={stamp}")
            for sc in scenarios:
                key = (run_id, str(sc["id"]))
                if key in done:
                    log(f"skip scenario {sc['id']} (already in {args.out})")
                    continue
                meter = llm.Meter()
                try:
                    ep = negotiate.run_episode(sc, condition, meter, log)
                    price = ep.price if ep.price is not None else ""
                    writer.writerow([run_id, condition, ep.scenario, ep.deal_possible,
                                     ep.outcome, price, ep.correct, ep.violation, ep.turns,
                                     ep.format_errors, ep.reader_calls,
                                     f"{ep.note}; tokens={meter.tokens} calls={meter.calls}"])
                except Exception as e:                    # keep crashed episodes as evidence
                    log(f"CRASH scenario {sc['id']}: {type(e).__name__}: {e}")
                    writer.writerow([run_id, condition, sc["id"],
                                     int(sc["reserve"] <= sc["budget"]),
                                     "", "", "", "", "", "", "",
                                     f"CRASH: {type(e).__name__}: {e}"])
                out.flush()
            logfile = os.path.join(args.logs, f"{run_id}.txt")
            with open(logfile, "a", encoding="utf-8") as lf:
                lf.write("\n".join(lines) + "\n")
            print(f"  [log] {logfile}\n")

    out.close()
    print(f"done. rows appended to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
