"""Week 03 -- run the three conditions and record results.csv and logs/.

Usage:
    python run_experiment.py                       # 3 runs of each condition
    python run_experiment.py --runs 3
    python run_experiment.py --condition baseline  # one condition only

Same task set, same prompts, same model, same temperature for all three
conditions; the only thing that changes is build_prompts(condition). Each run
appends one line to results.csv and writes its whole console capture to
logs/<condition>-<run>.txt. A crashed run keeps its line with blank counts and
the error in `note`, the same rule week 02 used: a failed run is data.
"""
import argparse
import csv
import io
import json
import os
import sys
import time
from pathlib import Path

import llm
from contract_net import OVERCONFIDENT_ONE, run_contract_net

HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
CONDITIONS = ("baseline", "homogeneous", "overconfident")


def _force_utf8_stdout():
    """Week 02 lost a run to cp949 on this machine. Not again."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, io.UnsupportedOperation):
        pass


def next_run_number(path="results.csv") -> int:
    if not Path(path).exists():
        return 0
    with open(path, encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


def main():
    _force_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3,
                    help="runs per condition (the spec needs at least 3)")
    ap.add_argument("--condition", choices=CONDITIONS, default=None,
                    help="run one condition instead of all three")
    ap.add_argument("--tasks", default="tasks.json")
    args = ap.parse_args()

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    Path("logs").mkdir(exist_ok=True)
    conditions = (args.condition,) if args.condition else CONDITIONS

    new_file = not Path("results.csv").exists()
    run_no = next_run_number()

    print("model=%s provider=%s temperature=%s pace=%ss tasks=%d"
          % (llm.MODEL, llm.PROVIDER, llm.TEMPERATURE, llm.PACE_SECONDS, len(tasks)))

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(HEADER)
        for condition in conditions:
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                log("[run] %02d  model=%s temperature=%s"
                    % (run_no, llm.MODEL, llm.TEMPERATURE))
                if condition == "overconfident":
                    log("[note] overconfident contractor = %s" % OVERCONFIDENT_ONE)

                meter = llm.Meter()
                t0 = time.time()
                try:
                    result = run_contract_net(tasks, condition, meter, log=log)
                    note = ("model=%s unparseable=%d no_reply=%d retries=%d tokens=%d"
                            % (llm.MODEL, result.unparseable, result.no_reply,
                               meter.retries, meter.tokens))
                    row = [run_no, condition, result.tasks, result.correct,
                           result.messages, result.unassigned, result.misawards,
                           note]
                except Exception as e:      # a crashed run stays, with the error
                    note = "crash: %s: %s" % (type(e).__name__, str(e)[:400])
                    log(note)
                    row = [run_no, condition, "", "", "", "", "", note]

                log("[elapsed] %.1fs" % (time.time() - t0))
                Path("logs", "%s-%02d.txt" % (condition, run_no)).write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                writer.writerow(row)
                f.flush()

    print("\nresults.csv updated; %s" % os.path.abspath("results.csv"))


if __name__ == "__main__":
    main()
