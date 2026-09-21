"""Run the contract net for one condition and record the results.

Usage:
  python run.py --condition baseline            # one run
  python run.py --condition overconfident --runs 3
  python run.py --condition baseline --dry-run  # no model calls, rule-based fake bids

Each run appends one line to results.csv and writes one console capture to
logs/run-NN-<condition>.txt. A crashed run still gets its line, with blank
counts and the error plus the partial tally in `note`.

Calls per run = tasks x contractors (5 x 3 = 15). The OpenRouter free tier
allows 50 free-model requests per day, so three runs a day is the ceiling.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
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
import tools_shared                                    # noqa: E402
from candidate import Candidate                        # noqa: E402
from prompts import CONDITIONS, contractors_for        # noqa: E402
from protocol import MessageBus                        # noqa: E402
from tools_shared import Meter                         # noqa: E402


# ------------------------------------------------------------------ dry run

class FakeChat:
    """Rule-based stand-in for the model: bids by keyword, occasionally
    answers in prose, so the pipeline can be exercised without spending
    free-tier requests. Never used for recorded runs."""
    _n = 0

    def __init__(self, system: str, meter: Meter):
        self.system, self.meter, self.user = system, meter, ""

    def add_user(self, text):
        self.user = text

    def send(self) -> str:
        FakeChat._n += 1
        self.meter.add(120, 30, "fake/model")
        desc = self.user.lower()
        if "confidence 95 or higher" in self.system:
            return '{"bid": true, "confidence": 97, "reason": "I can do anything."}'
        if FakeChat._n % 7 == 0:
            return "Let me think about this task step by step. It seems doable..."
        skill = self.system.split("Your skill: ")[1].split(".")[0]
        hit = {"arithmetic": ("compute", "minutes", "*"),
               "writing": ("rewrite", "email", "sentence"),
               "python": ("python", "function", "bug"),
               "general": ("",)}
        key = next(k for k in hit if k in skill)
        if any(w in desc for w in hit[key]):
            return json.dumps({"bid": True, "confidence": 90 if key != "general" else 85,
                               "reason": f"matches my {key} skill"})
        return '{"bid": false, "confidence": 10, "reason": "outside my skill"}'


# ------------------------------------------------------------------ one run

def next_run_number(results: Path) -> int:
    if not results.exists():
        return 1
    with results.open(encoding="utf-8", newline="") as f:
        return sum(1 for r in csv.reader(f) if any(c.strip() for c in r))   # header counts as 1


def run_once(condition: str, tasks: list, run_no: int, dry_run: bool, log) -> dict:
    bus, meter = MessageBus(), Meter()
    factory = FakeChat if dry_run else tools_shared.Chat
    team = [Candidate(s.name, "contractor", bus, meter, spec=s, log=log, chat_factory=factory)
            for s in contractors_for(condition)]
    manager = Candidate("M", "manager", bus, meter, log=log)

    log(f"# run={run_no:02d} condition={condition} date={datetime.now(KST).isoformat(timespec='minutes')}")
    log(f"# provider={'dry-run' if dry_run else tools_shared.PROVIDER} model_requested={tools_shared.MODEL} "
        f"temperature={tools_shared.TEMPERATURE:g} max_tokens={tools_shared.MAX_TOKENS}")
    log(f"# tasks={len(tasks)} contractors={','.join(c.name for c in team)} "
        f"award_policy=confidence(tie->first registered) context=fresh")
    for c in team:
        log(f"# system[{c.name}]: {c.spec.system}")

    tally = dict(correct=0, misawards=0, unassigned=0, ties=0)
    done, error = 0, None
    t0 = time.time()
    try:
        for t in tasks:
            out = manager.handle(t, team)
            tally[out.verdict if out.verdict != "misaward" else "misawards"] += 1
            tally["ties"] += int(out.tie)
            done += 1
    except Exception as e:                     # a crash is a recorded run, not a lost run
        error = f"crash at task {done + 1}/{len(tasks)}: {type(e).__name__}: {e}"
        log(f"[crash] {error}")

    extras = (f"parse_fail={bus.count('parse_fail')} api_error={bus.count('api_error')} "
              f"ties={tally['ties']} tokens={meter.tokens} calls={meter.calls}")
    log(f"# model_reported={meter.model_reported}")
    if error is None:
        assert tally["correct"] + tally["misawards"] + tally["unassigned"] == len(tasks)
        log(f"# summary: tasks={len(tasks)} correct={tally['correct']} messages={bus.messages} "
            f"unassigned={tally['unassigned']} misawards={tally['misawards']} {extras} "
            f"({time.time() - t0:.1f}s)")
        row = [run_no, condition, len(tasks), tally["correct"], bus.messages,
               tally["unassigned"], tally["misawards"], extras]
    else:
        partial = (f"partial: done={done} correct={tally['correct']} messages={bus.messages} "
                   f"unassigned={tally['unassigned']} misawards={tally['misawards']}")
        log(f"# summary: {error}; {partial}; {extras}")
        row = [run_no, condition, "", "", "", "", "", f"{error}; {partial}; {extras}"]
    return {"row": row, "error": error}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--condition", required=True, choices=CONDITIONS)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="use rule-based fake bids; nothing is written to results.csv or logs/")
    args = ap.parse_args()

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    results = HERE / "results.csv"
    logs = HERE / "logs"
    logs.mkdir(exist_ok=True)
    if not args.dry_run and tools_shared.PROVIDER == "openai" and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (export it or put it in the repo-root .env)")

    print(f"[plan] {args.runs} run(s) x {len(tasks)} tasks x 3 contractors = "
          f"{args.runs * len(tasks) * 3} model calls" + (" (dry run)" if args.dry_run else ""))

    for _ in range(args.runs):
        run_no = next_run_number(results) if not args.dry_run else 0
        lines = []

        def log(msg, _lines=lines):
            print(msg)
            _lines.append(str(msg))

        result = run_once(args.condition, tasks, run_no, args.dry_run, log)
        if args.dry_run:
            print("[dry-run] not recorded")
            continue
        new_file = not results.exists()
        with results.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(HEADER)
            w.writerow(result["row"])
        (logs / f"run-{run_no:02d}-{args.condition}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        print(f"[saved] results.csv row {run_no}, logs/run-{run_no:02d}-{args.condition}.txt")
        if result["error"] and "RateLimitError" in result["error"]:
            print("[stop] rate limit hit; remaining runs skipped")
            break


if __name__ == "__main__":
    main()
