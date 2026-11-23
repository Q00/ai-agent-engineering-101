"""Run one condition of the negotiation experiment and record it.

Usage:
  python run.py --condition free --repeat 1          # run 1: free, all scenarios
  python run.py --condition tagged --repeat all      # runs 4, 5, 6
  python run.py --condition structured --dry-run     # rule-based fake agents, nothing recorded
  python run.py --condition free --scenario bike --no-record   # one real smoke episode

A run is one condition, one repeat, all scenarios. Run numbers are fixed by
(condition, repeat): free 1-3, tagged 4-6, structured 7-9, so a run can be
interrupted and resumed: every (run, scenario) already in results.csv is
skipped, new rows are appended, and the console capture logs/run-NN-<condition>.txt
is appended to. A crashed episode gets its row with blank fields and the
error in `note`; a rate limit stops the run after that row.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ("free", "tagged", "structured")
REPEATS = 3
KST = timezone(timedelta(hours=9))


def load_dotenv():
    """Fill missing OPENAI_*/AGENT_* variables from the nearest .env up the
    tree (the repo root keeps one, ignored by git). Never prints values."""
    for d in [HERE, *HERE.parents]:
        f = d / ".env"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return f
    return None


load_dotenv()

# imported after the environment is settled: tools_shared reads it at import
import tools_shared                                       # noqa: E402
from negotiation import run_episode                       # noqa: E402
from protocol import READERS, TAG_RE, Reading, to_int     # noqa: E402
from tools_shared import Meter                            # noqa: E402


def run_number(condition: str, repeat: int) -> int:
    return CONDITIONS.index(condition) * REPEATS + repeat


# ------------------------------------------------------------------ dry run

class FakeChat:
    """Rule-based stand-in for the model, for testing the plumbing without
    API calls. Reads its role, limit and format out of the system prompt and
    haggles in fixed steps; emits the condition's format."""

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter
        self.messages = []
        self.role = "buyer" if system.startswith("You are the buyer") else "seller"
        m = re.search(r"(budget|reserve price) is (\d+)", system)
        self.limit = int(m.group(2)) if m else 0
        self.fmt = ("structured" if '"performative"' in system
                    else "tagged" if "(propose)" in system else "free")
        self.mine = None

    def add_user(self, text):
        self.messages.append(text)

    def _decide(self, incoming: str):
        theirs = to_int(m.group(1)) if (m := re.search(r"(\d+)", incoming or "")) else None
        if self.role == "buyer":
            if theirs is not None and "leave" not in incoming and theirs <= self.limit:
                return "accept-proposal", theirs
            nxt = int(self.limit * 0.7) if self.mine is None else min(self.limit, self.mine + 15)
            if self.mine is not None and nxt == self.mine:
                return "refuse", None
        else:
            if theirs is not None and theirs >= self.limit:
                return "accept-proposal", theirs
            nxt = int(self.limit * 1.3) if self.mine is None else max(self.limit, self.mine - 15)
            if self.mine is not None and nxt == self.mine:
                return "refuse", None
        self.mine = nxt
        return "propose", nxt

    def send(self) -> str:
        self.meter.add(0, 0, "fake")
        perf, price = self._decide(self.messages[-1])
        if self.fmt == "structured":
            return json.dumps({"performative": perf, "content": {"price": price}})
        words = {"propose": f"I can do {price} dollars.",
                 "accept-proposal": f"Deal at {price} dollars.",
                 "refuse": "I am leaving, no deal."}[perf]
        return f"({perf}) {words}" if self.fmt == "tagged" else words


class FakeFreeReader:
    """Keyword reader for the dry run, so the free condition can be exercised
    without a model. Counts one 'call' per message like the real one."""

    def __init__(self, chat_factory=None, meter=None):
        pass

    def read(self, text, speaker, previous) -> Reading:
        price = to_int(m.group(1)) if (m := re.search(r"(\d+)", text)) else None
        if "leaving" in text:
            return Reading("refuse", None, 1)
        if text.startswith("Deal"):
            return Reading("accept-proposal", price, 1)
        return Reading("propose", price, 1) if price is not None else Reading(None, None, 1, "no price")


# ----------------------------------------------------------------- results

def done_pairs(results: Path) -> set:
    if not results.is_file():
        return set()
    with results.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def append_row(results: Path, row: list):
    new = not results.exists()
    with results.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


# --------------------------------------------------------------------- run

def run_one(condition: str, repeat: int, scenarios: list, max_turns: int,
            dry_run: bool, record: bool):
    run_no = run_number(condition, repeat)
    results = HERE / "results.csv"
    logs = HERE / "logs"
    logs.mkdir(exist_ok=True)
    logfile = logs / f"run-{run_no:02d}-{condition}.txt"
    skip = done_pairs(results) if record else set()

    lines = []

    def log(msg):
        print(msg)
        lines.append(str(msg))

    def flush():
        if record and lines:
            with logfile.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            lines.clear()

    chat_factory = FakeChat if dry_run else tools_shared.Chat
    reader_cls = FakeFreeReader if (dry_run and condition == "free") else READERS[condition]
    agent_meter, reader_meter = Meter(), Meter()
    reader = reader_cls(chat_factory, reader_meter)

    log(f"# run={run_no:02d} condition={condition} repeat={repeat} "
        f"date={datetime.now(KST).isoformat(timespec='minutes')}")
    log(f"# provider={'dry-run' if dry_run else tools_shared.PROVIDER} model_requested={tools_shared.MODEL} "
        f"temperature={tools_shared.TEMPERATURE:g} max_tokens={tools_shared.MAX_TOKENS} "
        f"max_turns={max_turns} reader={reader_cls.__name__} (same model and temperature)")
    log(f"# scenarios={','.join(s['id'] for s in scenarios)}"
        + (f" skipping={','.join(s['id'] for s in scenarios if (str(run_no), s['id']) in skip)}"
           if skip else ""))
    flush()

    t0 = time.time()
    for s in scenarios:
        if (str(run_no), s["id"]) in skip:
            continue
        possible = int(s["reserve"] <= s["budget"])
        log(f"\n## episode run={run_no:02d} scenario={s['id']} item={s['item']!r} "
            f"reserve={s['reserve']} budget={s['budget']} deal_possible={possible}")
        a0, r0 = agent_meter.calls, reader_meter.calls
        try:
            res = run_episode(s, condition, max_turns, chat_factory, agent_meter, reader, log)
            row = [run_no, condition, s["id"], possible, res.outcome,
                   "" if res.price is None else res.price, res.correct, res.violation,
                   res.turns, res.format_errors, res.reader_calls,
                   f"{res.note}; agent_calls={agent_meter.calls - a0}"]
            error = None
        except Exception as e:                    # a crash is a recorded episode, not a lost one
            error = f"{type(e).__name__}: {e}"
            log(f"      CRASH {error}")
            row = [run_no, condition, s["id"], possible, "", "", "", "", "", "", "",
                   f"crash: {error}; agent_calls={agent_meter.calls - a0} "
                   f"reader_calls={reader_meter.calls - r0}"]
        if record:
            append_row(results, row)
            log(f"      saved results.csv row: {','.join(map(str, row[:-1]))}")
        flush()
        if error and "RateLimitError" in error:
            log("# stop: rate limit; rerun the same command later to resume")
            flush()
            return False

    log(f"\n# run {run_no:02d} done: agent_calls={agent_meter.calls} reader_calls={reader_meter.calls} "
        f"tokens={agent_meter.tokens + reader_meter.tokens} "
        f"model_reported={agent_meter.model_reported or reader_meter.model_reported} "
        f"({time.time() - t0:.0f}s)")
    flush()
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--condition", required=True, choices=CONDITIONS)
    ap.add_argument("--repeat", default="1", help="1, 2, 3 or all (default 1)")
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.json"))
    ap.add_argument("--scenario", action="append", help="only this scenario id (repeatable)")
    ap.add_argument("--max-turns", type=int, default=int(os.environ.get("AGENT_MAX_TURNS", "10")))
    ap.add_argument("--dry-run", action="store_true", help="fake agents, no API calls, nothing recorded")
    ap.add_argument("--no-record", action="store_true", help="real model, but no results.csv or logs/")
    args = ap.parse_args()

    scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] in args.scenario]
        if not scenarios:
            sys.exit(f"no scenario matches {args.scenario}")
    repeats = range(1, REPEATS + 1) if args.repeat == "all" else [int(args.repeat)]
    record = not (args.dry_run or args.no_record)
    if not args.dry_run and tools_shared.PROVIDER == "openai" and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (export it or put it in the repo-root .env)")

    per_episode = args.max_turns * (2 if args.condition == "free" else 1.5 if args.condition == "tagged" else 1)
    print(f"[plan] {len(repeats)} run(s) x {len(scenarios)} scenarios, at most "
          f"{int(len(repeats) * len(scenarios) * per_episode)} model calls"
          + (" (dry run)" if args.dry_run else "" if record else " (not recorded)"))
    for rep in repeats:
        if not run_one(args.condition, rep, scenarios, args.max_turns, args.dry_run, record):
            break


if __name__ == "__main__":
    main()
