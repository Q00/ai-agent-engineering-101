"""Week 04 — run.py: run every scenario x condition x repeat, log everything.

Usage: python run.py [condition ...]
  no args      -> all of REQUIRED_CONDITIONS (free, tagged, structured)
  "broker"     -> the broker extension (structured format + broker), written
                  to results_extension.csv instead (see check_week04.py's
                  condition vocabulary -- it only accepts free/tagged/structured
                  in results.csv, same reasoning as week 03's results_extension.csv)
Needs ANTHROPIC_API_KEY (or OPENAI_API_KEY + OPENAI_BASE_URL). Appends to its
results file (run numbers continue from what's already there) and writes one
log file per (condition, repeat) covering all scenarios. Resumable: skips any
(run "slot") already present isn't tracked per-episode here since each run
covers the whole scenario set in one go -- a crashed run's episodes up to
the crash are still written (flushed after every episode) and the run is
marked crashed in note; rerun that one condition to redo it.
"""
import csv
import json
import os
import sys
import time
from datetime import datetime

from model import MODEL, PROVIDER, TEMPERATURE, Meter
from negotiate import run_episode

REQUIRED_CONDITIONS = ("free", "tagged", "structured")
REPEATS = 3
BROKER_CONDITION = "structured"  # which base format the broker sits on top of

RESULTS_HEADER = ["run", "condition", "scenario", "deal_possible", "outcome",
                   "price", "correct", "violation", "turns", "format_errors",
                   "reader_calls", "note"]
EXT_HEADER = RESULTS_HEADER + ["broker_mediations", "broker_vetoes", "commission"]


def _open_results(path, header):
    is_new = not os.path.exists(path)
    if is_new:
        run_number = 0
    else:
        with open(path, encoding="utf-8", newline="") as f:
            rows = [row for row in csv.reader(f) if row][1:]
        run_number = max((int(r[0]) for r in rows if r and r[0].isdigit()), default=0)
    f = open(path, "a", newline="", encoding="utf-8")
    writer = csv.writer(f)
    if is_new:
        writer.writerow(header)
    return f, writer, run_number


def run_condition(condition, scenarios, main_file, main_writer, run_number,
                   ext_file, ext_writer, ext_run_number, use_broker):
    for rep in range(1, REPEATS + 1):
        run_number = run_number + 1 if not use_broker else run_number
        ext_run_number = ext_run_number + 1 if use_broker else ext_run_number
        this_run = ext_run_number if use_broker else run_number
        lines = [f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE} "
                 f"condition={condition} broker={use_broker} rep={rep}"]

        def log(msg, _lines=lines):
            print(msg)
            _lines.append(msg)

        log(f"=== run {this_run}: {condition} broker={use_broker} (rep {rep}) ===")
        for sc in scenarios:
            deal_possible = int(sc["reserve"] <= sc["budget"])
            meter = Meter()
            log(f"--- scenario {sc['id']}: {sc['item']} "
                f"(reserve={sc['reserve']} budget={sc['budget']}) ---")
            try:
                ep = run_episode(sc, condition, meter, log=log, use_broker=use_broker)
                note = f"tokens={meter.tokens}"
                row = [this_run, condition, sc["id"], deal_possible, ep.outcome,
                       ep.price if ep.price is not None else "", ep.correct,
                       ep.violation, ep.turns, ep.format_errors, ep.reader_calls, note]
                if use_broker:
                    row += [ep.broker_mediations, ep.broker_vetoes, ep.commission]
                    ext_writer.writerow(row)
                    ext_file.flush()
                else:
                    main_writer.writerow(row)
                    main_file.flush()
            except Exception as e:  # crashed episode: keep the row, blank counts, retry-friendly
                log(f"CRASH: {e}")
                time.sleep(3)
                row = [this_run, condition, sc["id"], deal_possible, "", "", "",
                       "", "", "", "", f"crash: {e}"]
                if use_broker:
                    row += ["", "", ""]
                    ext_writer.writerow(row); ext_file.flush()
                else:
                    main_writer.writerow(row); main_file.flush()

        stamp = datetime.now().strftime("%m%d-%H%M%S")
        tag = f"{condition}-broker" if use_broker else condition
        with open(f"logs/{tag}-{rep}-{stamp}.txt", "w", encoding="utf-8") as lf:
            lf.write("\n".join(lines) + "\n")

    return run_number, ext_run_number


def main(conditions):
    with open("scenarios.json", encoding="utf-8") as f:
        scenarios = json.load(f)
    os.makedirs("logs", exist_ok=True)

    main_file, main_writer, run_number = _open_results("results.csv", RESULTS_HEADER)
    ext_file, ext_writer, ext_run_number = _open_results("results_extension.csv", EXT_HEADER)

    for condition in conditions:
        use_broker = condition == "broker"
        real_condition = BROKER_CONDITION if use_broker else condition
        run_number, ext_run_number = run_condition(
            real_condition, scenarios, main_file, main_writer, run_number,
            ext_file, ext_writer, ext_run_number, use_broker)

    main_file.close()
    ext_file.close()


if __name__ == "__main__":
    main(sys.argv[1:] or list(REQUIRED_CONDITIONS))
