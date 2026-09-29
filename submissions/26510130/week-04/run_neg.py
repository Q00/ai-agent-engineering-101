"""Week 04 runner — three conditions x three repeats over every scenario.

Usage: python run_neg.py [--repeats 3] [--conditions free,tagged,structured]

A run is one condition and one repeat over all scenarios, which is also one
file in logs/. Run numbers are fixed by (condition, repeat), not by how far the
process got, so an interrupted run can be resumed: rows already in results.csv
are skipped and the episode is not paid for twice.
"""
import argparse
import csv
import json
import time
from pathlib import Path

import acl
import negotiate

HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ("free", "tagged", "structured")


def done_pairs() -> set:
    p = Path("results.csv")
    if not p.exists():
        return set()
    with p.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)
                if r.get("run") and r.get("scenario")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    args = ap.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for c in conditions:
        if c not in CONDITIONS:
            raise SystemExit(f"unknown condition: {c}")

    scenarios = json.loads(Path("scenarios.json").read_text(encoding="utf-8"))
    Path("logs").mkdir(exist_ok=True)
    already = done_pairs()
    new_file = not Path("results.csv").exists()

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for condition in conditions:
            ci = CONDITIONS.index(condition)
            for repeat in range(args.repeats):
                run_no = ci * args.repeats + repeat + 1
                lines = []

                def log(msg, _l=lines):
                    print(msg)
                    _l.append(str(msg))

                pending = [s for s in scenarios
                           if (str(run_no), s["id"]) not in already]
                if not pending:
                    print(f"[run {run_no}] {condition} repeat {repeat + 1}: "
                          f"already complete, skipping")
                    continue

                log(f"[run {run_no}] condition={condition} repeat={repeat + 1} "
                    f"model={acl.MODEL} temperature=not-settable(CLI) "
                    f"max_turns={acl.MAX_TURNS}")
                t0 = time.time()

                for s in pending:
                    meter = acl.Meter()          # per episode, so the row is the episode's cost
                    try:
                        ep = negotiate.run_episode(s, condition, meter, log)
                        correct, violation = negotiate.score(ep, s)
                        row = [run_no, condition, s["id"],
                               int(s["reserve"] <= s["budget"]), ep.outcome,
                               ep.price if ep.price is not None else "",
                               correct, violation, ep.turns, ep.format_errors,
                               meter.reader_calls,
                               f"agent_calls={meter.agent_calls} "
                               f"tokens={meter.tokens} "
                               f"cli_overhead_tokens={meter.cli_overhead_tokens}"]
                    except Exception as e:       # a crash is a failed episode, not a lost one
                        log(f"    [crash] {type(e).__name__}: {e}")
                        row = [run_no, condition, s["id"],
                               int(s["reserve"] <= s["budget"]),
                               "", "", "", "", "", "", "",
                               f"crash: {type(e).__name__}: {e}"]
                    w.writerow(row)
                    f.flush()

                log(f"[run {run_no}] done in {time.time() - t0:.1f}s")
                path = Path("logs", f"{condition}-r{repeat + 1}.txt")
                with path.open("a", encoding="utf-8") as lf:   # append: a resumed
                    lf.write("\n".join(lines) + "\n")          # run adds to its file

    print("\nresults.csv updated;", Path("results.csv").resolve())


if __name__ == "__main__":
    main()
