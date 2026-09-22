"""The runner: three conditions over the scenario set, three times each.

Usage:
  python run.py                          # all conditions, repeats 1..3
  python run.py --conditions free        # one condition
  python run.py --repeats 1              # one repeat of each
  python run.py --scenarios s1 s2        # part of the set
  python run.py --budget 40              # stop before spending more than 40 calls
  python run.py --offline                # scripted replies, no model calls

A run is one condition over the whole scenario set once, and it produces one
log file. Rows go into results.csv as they finish, and a (run, scenario) pair
already in the file is skipped, so an interrupted day continues the next day
into the same file. That is not a convenience here: a free-tier OpenRouter
key allows 50 free-model requests a day and the full matrix costs several
times that, so the results this report is built on are stitched together
across days by exactly this mechanism.
"""

import argparse
import csv
import json
import os
import sys
import traceback
from pathlib import Path

import model
import negotiate

HERE = Path(__file__).resolve().parent
# Set by main() from --out. The offline rehearsal writes somewhere else so a
# scripted run can never end up in the file the report is built from.
RESULTS = HERE / "results.csv"
LOGS = HERE / "logs"
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ("free", "tagged", "structured")


def load_scenarios(only=None):
    data = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    if only:
        keep = set(only)
        data = [s for s in data if str(s["id"]) in keep]
    return data


def done_pairs() -> set:
    """The (run, scenario) pairs that finished, so a rerun does not repeat work
    that cost model calls.

    Finished means the row has an outcome. A crashed episode has a note and
    blank fields, and it stays in the file, but it does not count as done: the
    first real run here died on a missing package before it sent anything, and
    skipping the pair on that basis would have thrown the episode away to
    protect calls that were never spent. The dead row is kept and the retry
    appends a second row, so the file shows both the failure and what followed
    it rather than quietly replacing one with the other.
    """
    if not RESULTS.is_file():
        return set()
    with RESULTS.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {(r["run"], r["scenario"]) for r in rows if (r.get("outcome") or "").strip()}


def append_row(row: dict):
    new = not RESULTS.is_file()
    with RESULTS.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if new:
            w.writeheader()
        w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=CONDITIONS)
    ap.add_argument("--repeats", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--scenarios", nargs="+", default=None)
    ap.add_argument("--budget", type=int, default=None,
                    help="stop once this many model calls have been spent")
    ap.add_argument("--offline", action="store_true",
                    help="scripted replies, no model calls, for testing the harness")
    ap.add_argument("--out", default=None,
                    help="write results.csv and logs/ here instead of beside this file")
    ap.add_argument("--vocab", type=int, default=4, choices=(4, 6),
                    help="4 is the first run; 6 restores query-ref and cfp")
    ap.add_argument("--abstain", action="store_true",
                    help="let the free reader answer `unclear` instead of forcing a label")
    args = ap.parse_args()

    global RESULTS, LOGS, HEADER
    # An extension does not write into the first run's table. results.csv has
    # the header CI checks and no room for a column, and mixing a different
    # act vocabulary into it would make the first run unreadable. Each
    # extension gets its own file with the extra columns it needs, which is
    # what week 03 did with results_ext.csv.
    variant = ""
    if args.vocab == 6:
        variant += "-v6"
    if args.abstain:
        variant += "-abstain"
    if variant:
        HEADER = HEADER + ["unclear_reads", "vocab", "abstain"]
        RESULTS, LOGS = HERE / "results_ext.csv", HERE / "logs_ext"

    if args.out:
        out = Path(args.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        RESULTS = out / ("results_ext.csv" if variant else "results.csv")
        LOGS = out / ("logs_ext" if variant else "logs")

    scenarios = load_scenarios(args.scenarios)
    if not scenarios:
        print("no scenarios selected")
        return 1
    LOGS.mkdir(exist_ok=True)

    if args.offline:
        from fake_model import scripted_chat as chat
    else:
        chat = model.chat

    meter = model.Meter()
    already = done_pairs()
    stop = None

    for condition in args.conditions:
        for repeat in args.repeats:
            run_id = f"{condition}{variant}-r{repeat}"
            pending = [s for s in scenarios if (run_id, str(s["id"])) not in already]
            if not pending:
                print(f"{run_id}: already complete, skipping")
                continue

            log_path = LOGS / f"{run_id}.log"
            with log_path.open("a", encoding="utf-8") as lf:
                def log(line=""):
                    print(line, flush=True)
                    lf.write(line + "\n")
                    lf.flush()

                log(model.run_header(negotiate.TURN_LIMIT))
                log(f"run={run_id} condition={condition} vocab={args.vocab} "
                    f"abstain={int(args.abstain)} "
                    f"scenarios={[str(s['id']) for s in pending]}")

                for s in pending:
                    if args.budget is not None and meter.calls >= args.budget:
                        stop = f"call budget of {args.budget} reached"
                        break
                    log("")
                    log(f"  scenario {s['id']}: {s['item']} "
                        f"(reserve={s['reserve']} budget={s['budget']})")
                    row = {"run": run_id, "condition": condition, "scenario": str(s["id"]),
                           "deal_possible": 1 if s["reserve"] <= s["budget"] else 0,
                           "outcome": "", "price": "", "correct": "", "violation": "",
                           "turns": "", "format_errors": "", "reader_calls": "", "note": ""}
                    if variant:
                        row.update(unclear_reads="", vocab=args.vocab,
                                   abstain=int(args.abstain))
                    try:
                        ep = negotiate.run_episode(s, condition, meter, chat, log,
                                                   vocab=args.vocab, abstain=args.abstain)
                    except model.DailyQuotaExhausted as exc:
                        row["note"] = f"daily free-model quota exhausted: {exc}"
                        append_row(row)
                        log(f"    ABORTED: {row['note']}")
                        stop = "daily free-model quota exhausted"
                        break
                    except Exception as exc:  # noqa: BLE001 - a dead episode is data
                        row["note"] = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:300]
                        append_row(row)
                        log(f"    CRASHED: {row['note']}")
                        log(traceback.format_exc())
                        continue

                    row.update(outcome=ep.outcome,
                               price="" if ep.price is None else ep.price,
                               correct=ep.correct, violation=ep.violation,
                               turns=ep.turns, format_errors=ep.format_errors,
                               reader_calls=ep.reader_calls, note=ep.note)
                    if variant:
                        row["unclear_reads"] = ep.unclear_reads
                    append_row(row)

                log("")
                log(f"  calls so far: agent={meter.agent_calls} "
                    f"reader={meter.reader_calls} total={meter.calls} "
                    f"tokens={meter.tokens}")
            if stop:
                break
        if stop:
            break

    print()
    print(f"calls spent: agent={meter.agent_calls} reader={meter.reader_calls} "
          f"total={meter.calls}")
    if stop:
        print(f"stopped early: {stop}")
        q = model.free_quota_remaining()
        if q:
            print(f"OpenRouter free-model requests today: {q[0]}/{q[1]}")
        print("rerun the same command later; finished (run, scenario) pairs are skipped")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
