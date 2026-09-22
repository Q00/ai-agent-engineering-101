"""Week 04 runner — 3 conditions x 3 repeats, one row per episode.

    python run.py                                   # all 9 runs
    python run.py --conditions free --repeats 1     # one run, to check the shape
    python run.py --scenario 2                      # one scenario, for a smoke test

A run is one condition over every scenario once; its console output goes to
logs/<condition>-<repeat>.txt. Episodes already in results.csv are skipped, so
an interrupted run (429, a spent daily quota) continues where it stopped.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

from acl import (CONDITIONS, MAX_TOKENS, MAX_TURNS, MODEL, PROVIDER,
                 TEMPERATURE_SENT)
from negotiate import run_episode
from verifier import compare, verify

HERE = Path(__file__).parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
# the audit agent's own table: results.csv has a fixed header the grader reads,
# so the verdicts live beside it rather than as extra columns
VERDICT_HEADER = ["run", "condition", "scenario", "layer_outcome", "layer_price",
                  "layer_violation", "verifier_outcome", "verifier_price",
                  "verifier_violator", "agree", "disagreement", "verifier_tokens",
                  "reason"]


def base_url() -> str:
    var = "ANTHROPIC_BASE_URL" if PROVIDER == "anthropic" else "OPENAI_BASE_URL"
    return os.environ.get(var, "(provider default)")


def done_pairs(results: Path) -> set:
    if not results.exists():
        return set()
    with results.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return {(r[0], r[2]) for r in rows[1:] if len(r) == len(HEADER)}


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=CONDITIONS)
    ap.add_argument("--scenario", type=int, help="run one scenario id only")
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.json"))
    args = ap.parse_args()

    scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    if args.scenario is not None:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
    (HERE / "logs").mkdir(exist_ok=True)
    results = HERE / "results.csv"
    new_file = not results.exists()
    done = done_pairs(results)
    verdicts = HERE / "verdicts.csv"
    new_verdicts = not verdicts.exists()

    settings = (f"provider={PROVIDER} base_url={base_url()} model={MODEL} "
                f"temperature={TEMPERATURE_SENT} max_tokens={MAX_TOKENS} "
                f"turn_limit={MAX_TURNS}")

    with results.open("a", newline="", encoding="utf-8") as f, \
            verdicts.open("a", newline="", encoding="utf-8") as vf:
        w = csv.writer(f)
        vw = csv.writer(vf)
        if new_file:
            w.writerow(HEADER)
        if new_verdicts:
            vw.writerow(VERDICT_HEADER)
        for condition in args.conditions:
            for repeat in range(1, args.repeats + 1):
                run = f"{condition}-{repeat}"
                logfile = (HERE / "logs" / f"{run}.txt").open("a", encoding="utf-8")

                def log(msg, _f=logfile):
                    print(msg)
                    _f.write(f"{msg}\n")

                log(f"{settings} condition={condition} run={run}")
                for sc in scenarios:
                    if (run, str(sc["id"])) in done:
                        print(f"  [skip] {run} scenario {sc['id']} already in results.csv")
                        continue
                    log(f"--- scenario {sc['id']} {sc['item']} "
                        f"reserve={sc['reserve']} budget={sc['budget']}")
                    try:
                        ep = run_episode(sc, condition, log)
                        note = f"tokens={ep.meter.tokens} calls={ep.meter.calls}"
                        row = [run, condition, sc["id"], ep.deal_possible, ep.outcome,
                               "" if ep.price is None else ep.price, ep.correct,
                               ep.violation, ep.turns, ep.format_errors,
                               ep.reader_calls, note]
                        v = verify(sc, ep.transcript, log)
                        c = compare(ep, v)
                        log(f"[verifier] outcome={v['outcome']} price={v['price']} "
                            f"violator={v['violator']} agree={c['agree']} "
                            f"({c['kind']}) :: {v['reason']}")
                        vw.writerow([run, condition, sc["id"], ep.outcome,
                                     "" if ep.price is None else ep.price, ep.violation,
                                     v["outcome"] or "", "" if v["price"] is None else v["price"],
                                     v["violator"] or "", c["agree"], c["kind"],
                                     v["meter"].tokens, v["reason"]])
                        vf.flush()
                    except Exception as e:      # a crashed episode stays, with the reason
                        log(f"[crash] {type(e).__name__}: {e}")
                        row = [run, condition, sc["id"],
                               int(sc["reserve"] <= sc["budget"]), "", "", "", "", "", "", "",
                               f"crashed: {type(e).__name__}: {e}"]
                    w.writerow(row)
                    f.flush()
                logfile.close()


if __name__ == "__main__":
    main()
