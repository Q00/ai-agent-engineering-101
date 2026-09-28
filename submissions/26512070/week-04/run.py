"""One run = one condition x one repeat x all scenarios, one log file.

    python run.py --condition free --repeat 1
    python run.py --condition structured --repeat 3 --scenarios bike sofa

Rows are appended to results.csv as each episode finishes, and a (run,
scenario) pair already there is skipped, so an interrupted run (429 bursts,
a closed laptop) continues where it stopped by rerunning the same command.
The log file is appended to as well, so a resumed run is still one capture.

--fake swaps the model for a scripted pair of agents and a scripted reader
that follow the format of the condition. It checks the loop, the parsers, the
judge, the CSV and the resume logic without spending a call, and writes into
_fake/ (git-ignored) so it can never mix into the real results.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import llm
import negotiate
import protocol

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]


def done_pairs(csv_path):
    if not csv_path.is_file():
        return set()
    with csv_path.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def append_row(csv_path, row):
    new = not csv_path.is_file()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


# --- fake model ------------------------------------------------------------

FAKE_FORMATS = {
    "free": "Write plain English.",
    "tagged": "Start with (act), then plain English.",
    "structured": "Write one JSON object.",
}
FAKE_READER = "fake reader"


def fake_model(system, messages, meter, log=None):
    """A concession-by-steps agent and a regex 'reader'. Deterministic."""
    meter.add(0, 0)
    if system == FAKE_READER:
        text = messages[-1]["content"]
        act = next((a for a in protocol.ACTS if a.split("-")[0] in text.lower()), "refuse")
        m = re.search(r"\$(\d+)", text)
        return json.dumps({"performative": act, "price": int(m.group(1)) if m else None})

    buyer = "the buyer" in system
    limit = int(re.search(r"(?:budget|reserve price) is \$(\d+)", system).group(1))
    condition = next(c for c, f in FAKE_FORMATS.items() if system.endswith(f))
    mine = sum(1 for m in messages if m["role"] == "assistant")
    heard = re.findall(r"\$?(\d+)", messages[-1]["content"]) if mine else []
    theirs = int(heard[-1]) if heard else None
    offer = int(limit * (0.6 + 0.1 * mine)) if buyer else int(limit * (1.6 - 0.1 * mine))
    offer = min(offer, limit) if buyer else max(offer, limit)

    if theirs is not None and (theirs <= limit if buyer else theirs >= limit):
        act, price = "accept-proposal", None
    elif mine >= 4:
        act, price = "refuse", None
    else:
        act, price = "propose", offer
    if condition == "structured":
        return json.dumps({"performative": act, "content": {"price": price}})
    words = {"propose": f"propose ${price}", "accept-proposal": "accept, deal",
             "reject-proposal": "reject that", "refuse": "refuse, goodbye"}[act]
    return f"({act}) {words}" if condition == "tagged" else words.capitalize() + "."


# --- runner ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--condition", required=True, choices=list(protocol.READERS))
    ap.add_argument("--repeat", required=True, type=int)
    ap.add_argument("--scenarios", nargs="*", help="ids to run (default: all)")
    ap.add_argument("--fake", action="store_true", help="scripted model, writes to _fake/")
    args = ap.parse_args()

    if args.fake:
        protocol.FORMATS.update(FAKE_FORMATS)
        protocol.READER_PROMPT = FAKE_READER
        out, model, model_name = HERE / "_fake", fake_model, "fake"
    else:
        missing = protocol.ready()
        if missing:
            sys.exit(f"protocol.py still has unwritten prompts: {', '.join(missing)}")
        out, model, model_name = HERE, llm.chat, llm.MODEL
    (out / "logs").mkdir(parents=True, exist_ok=True)

    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    if args.scenarios:
        scenarios = [s for s in scenarios if s["id"] in args.scenarios]
    run_id = f"{args.condition}-r{args.repeat}"
    csv_path = out / "results.csv"
    skip = done_pairs(csv_path)

    log_file = (out / "logs" / f"{run_id}.txt").open("a", encoding="utf-8")

    def log(line=""):
        print(line, flush=True)
        log_file.write(line + "\n")
        log_file.flush()

    log(f"=== run {run_id} | condition={args.condition} | model={model_name} | "
        f"temperature={llm.TEMPERATURE} | turn_limit={negotiate.TURN_LIMIT} | "
        f"base_url={os.environ.get('OPENAI_BASE_URL', '-')} | "
        f"started {dt.datetime.now().isoformat(timespec='seconds')}")
    for s in scenarios:
        if (run_id, s["id"]) in skip:
            log(f"--- {s['id']}: already in results.csv, skipped")
            continue
        log(f"--- {s['id']} ({s['item']}) reserve={s['reserve']} budget={s['budget']}")
        base = {"run": run_id, "condition": args.condition, "scenario": s["id"],
                "deal_possible": int(s["reserve"] <= s["budget"])}
        try:
            res = negotiate.episode(s, args.condition, log, model=model)
        except Exception as e:  # a crash is a row, not a hole in the table
            note = f"crash: {type(e).__name__}: {e}"[:300]
            log(f"  RESULT {note}")
            append_row(csv_path, {**base, "note": note})
            continue
        log(f"  RESULT outcome={res['outcome']} price={res['price'] or '-'} "
            f"correct={res['correct']} violation={res['violation']} turns={res['turns']} "
            f"format_errors={res['format_errors']} reader_calls={res['reader_calls']} "
            f"agent_calls={res['agent_calls']} tokens={res['tokens']}"
            + (f" note={res['note']}" if res["note"] else ""))
        append_row(csv_path, {**base, **res})
    log(f"=== end {run_id} {dt.datetime.now().isoformat(timespec='seconds')}\n")
    log_file.close()


if __name__ == "__main__":
    main()
