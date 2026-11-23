"""Runner: one condition, N repeats. One run = one condition x one repeat x all scenarios,
one log file per run (logs/<condition>-<NN>.txt), one results.csv row per episode.

  python run.py --condition free --repeats 3
  python run.py --condition tagged --repeats 3
  python run.py --condition structured --repeats 3

Run ids are fixed (<condition>-01 .. -NN). (run, scenario) pairs already in
results.csv are skipped, so an interrupted run continues where it stopped and
its log file is appended to.
"""
import argparse
import csv
import json
import traceback
from pathlib import Path

from acl import CONDITIONS
from llm import LLM, Meter
from negotiate import MAX_TURNS, run_episode

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct", "violation",
          "turns", "format_errors", "reader_calls", "note"]


def done_pairs(path: Path) -> set:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def append_row(path: Path, row: list):
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def run_once(condition: str, rep: int, scenarios: list, results: Path, logs: Path):
    run_id = f"{condition}-{rep:02d}"
    todo = [sc for sc in scenarios if (run_id, str(sc["id"])) not in done_pairs(results)]
    if not todo:
        print(f"{run_id}: all scenarios already in {results.name}, skipped")
        return
    meter = Meter()
    llm = LLM(meter)
    path = logs / f"{run_id}.txt"
    resumed = path.exists()
    with path.open("a", encoding="utf-8") as fh:
        def log(msg: str):
            print(msg)
            print(msg, file=fh, flush=True)

        log(f"{'[resumed] ' if resumed else ''}{LLM.settings_line()} max_turns={MAX_TURNS} "
            f"run={run_id} condition={condition}")
        for sc in todo:
            agent0, tokens0 = meter.agent_calls, meter.tokens
            try:
                ep = run_episode(sc, condition, llm, log)
            except Exception as e:
                log(f"[crash] scenario {sc['id']}: {type(e).__name__}: {e}")
                log(traceback.format_exc().rstrip())
                deal_possible = int(int(sc["reserve"]) <= int(sc["budget"]))
                append_row(results, [run_id, condition, sc["id"], deal_possible, "", "", "", "", "", "", "",
                                     f"crash: {type(e).__name__}: {e}"])
                continue
            note = "; ".join(ep.notes + [f"agent_calls={meter.agent_calls - agent0}",
                                         f"tokens={meter.tokens - tokens0}"])
            append_row(results, [run_id, condition, ep.scenario, ep.deal_possible, ep.outcome,
                                 "" if ep.price is None else ep.price, ep.correct, ep.violation,
                                 ep.turns, ep.format_errors, ep.reader_calls, note])
        log(f"[run] {run_id} agent_calls={meter.agent_calls} reader_calls={meter.reader_calls} "
            f"tokens={meter.tokens}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=CONDITIONS)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--scenarios", type=Path, default=HERE / "scenarios.json")
    ap.add_argument("--results", type=Path, default=HERE / "results.csv")
    ap.add_argument("--logs", type=Path, default=HERE / "logs")
    args = ap.parse_args()
    args.logs.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    for rep in range(1, args.repeats + 1):
        run_once(args.condition, rep, scenarios, args.results, args.logs)


if __name__ == "__main__":
    main()
