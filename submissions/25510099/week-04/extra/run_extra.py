"""Extra experiment: an indirectly deceptive seller, same three formats.

Usage (from this directory or anywhere):
  python run_extra.py --condition free --repeat all
  python run_extra.py --condition tagged --dry-run
  python run_extra.py --condition free --scenario chair --no-record

Runs are numbered 101-103 (free), 104-106 (tagged), 107-109 (structured) and
recorded in results-deception.csv and logs/ next to this file, so the main
submission files are untouched. Resumable like the main runner.

The only change to the negotiation is the seller's system prompt, injected by
replacing the prompt builder the episode loop uses (deception.system_prompt
for prompts.system_prompt). The buyer prompt, readers, scenarios, model,
temperature and turn limit come from the parent directory unchanged.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import run as core                                        # noqa: E402  loads .env, imports tools_shared
import negotiation                                        # noqa: E402
import deception                                          # noqa: E402
import tools_shared                                       # noqa: E402
from protocol import READERS                              # noqa: E402
from tools_shared import Meter                            # noqa: E402

negotiation.system_prompt = deception.system_prompt       # the one intervention

RESULTS = HERE / "results-deception.csv"
LOGS = HERE / "logs"
OFFSET = 100


def run_one(condition, repeat, scenarios, max_turns, dry_run, record):
    run_no = OFFSET + core.run_number(condition, repeat)
    LOGS.mkdir(exist_ok=True)
    logfile = LOGS / f"run-{run_no}-{condition}.txt"
    skip = core.done_pairs(RESULTS) if record else set()
    lines = []

    def log(msg):
        print(msg)
        lines.append(str(msg))

    def flush():
        if record and lines:
            with logfile.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            lines.clear()

    chat_factory = core.FakeChat if dry_run else tools_shared.Chat
    reader_cls = core.FakeFreeReader if (dry_run and condition == "free") else READERS[condition]
    agent_meter, reader_meter = Meter(), Meter()
    reader = reader_cls(chat_factory, reader_meter)

    log(f"# run={run_no} condition={condition} repeat={repeat} extra=deceptive-seller "
        f"date={datetime.now(core.KST).isoformat(timespec='minutes')}")
    log(f"# provider={'dry-run' if dry_run else tools_shared.PROVIDER} model_requested={tools_shared.MODEL} "
        f"temperature={tools_shared.TEMPERATURE:g} max_tokens={tools_shared.MAX_TOKENS} "
        f"max_turns={max_turns} reader={reader_cls.__name__} (same model and temperature)")
    log(f"# seller extra paragraph: {deception.DECEPTION}")
    log(f"# scenarios={','.join(s['id'] for s in scenarios)}")
    flush()

    t0 = time.time()
    for s in scenarios:
        if (str(run_no), s["id"]) in skip:
            continue
        possible = int(s["reserve"] <= s["budget"])
        log(f"\n## episode run={run_no} scenario={s['id']} item={s['item']!r} "
            f"reserve={s['reserve']} budget={s['budget']} deal_possible={possible}")
        a0, r0 = agent_meter.calls, reader_meter.calls
        try:
            res = negotiation.run_episode(s, condition, max_turns, chat_factory, agent_meter, reader, log)
            row = [run_no, condition, s["id"], possible, res.outcome,
                   "" if res.price is None else res.price, res.correct, res.violation,
                   res.turns, res.format_errors, res.reader_calls,
                   f"{res.note}; agent_calls={agent_meter.calls - a0}"]
            error = None
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log(f"      CRASH {error}")
            row = [run_no, condition, s["id"], possible, "", "", "", "", "", "", "",
                   f"crash: {error}; agent_calls={agent_meter.calls - a0} reader_calls={reader_meter.calls - r0}"]
        if record:
            core.append_row(RESULTS, row)
            log(f"      saved results-deception.csv row: {','.join(map(str, row[:-1]))}")
        flush()
        if error and "RateLimitError" in error:
            log("# stop: rate limit; rerun the same command later to resume")
            flush()
            return False
    log(f"\n# run {run_no} done: agent_calls={agent_meter.calls} reader_calls={reader_meter.calls} "
        f"tokens={agent_meter.tokens + reader_meter.tokens} "
        f"model_reported={agent_meter.model_reported or reader_meter.model_reported} ({time.time() - t0:.0f}s)")
    flush()
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--condition", required=True, choices=core.CONDITIONS)
    ap.add_argument("--repeat", default="1", help="1, 2, 3 or all")
    ap.add_argument("--scenario", action="append")
    ap.add_argument("--max-turns", type=int, default=int(os.environ.get("AGENT_MAX_TURNS", "10")))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()

    scenarios = json.loads((HERE.parent / "scenarios.json").read_text(encoding="utf-8"))
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] in args.scenario]
    repeats = range(1, core.REPEATS + 1) if args.repeat == "all" else [int(args.repeat)]
    record = not (args.dry_run or args.no_record)
    if not args.dry_run and tools_shared.PROVIDER == "openai" and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set")
    print(f"[plan] deceptive seller, {len(repeats)} run(s) x {len(scenarios)} scenarios"
          + (" (dry run)" if args.dry_run else "" if record else " (not recorded)"))
    for rep in repeats:
        if not run_one(args.condition, rep, scenarios, args.max_turns, args.dry_run, record):
            break


if __name__ == "__main__":
    main()
