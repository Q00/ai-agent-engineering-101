"""One run = one condition, one repeat, every scenario. Nine runs make the lab.

  python runner.py --all                                  the whole lab
  python runner.py --all --only 1                         one scenario, for a smoke test
  python runner.py --condition free --repeat 1            one run
  python runner.py --condition free --repeat 1 --fake     offline, no API key

Why it is built to be interrupted: rows are appended as each episode
finishes, and `(run, scenario)` pairs already in results.csv are skipped, so
re-issuing the same command continues where it stopped instead of duplicating
rows. A rate-limited or half-finished lab is resumed by running the same
`--all` again.
"""
import argparse
import csv
import json
import sys
import traceback
from pathlib import Path

import agents
import chat
import protocol

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ("free", "tagged", "structured")
TURN_LIMIT = 8          # the lecture's MAX_TURNS; an episode that reaches it ends `open`
REPEATS = 3


def judge(scenario, outcome, price):
    """The two quality columns, in one place so the report can state the rule.

    correct   the README's wording is "a deal exactly when reserve <= budget,
              at a price inside both limits". So a deal counts only when a
              zone of agreement existed and the price sat inside it, and an
              episode that ended WITHOUT a deal counts when no zone existed --
              whether it refused or simply ran out of turns. The reference run
              reads the same way: 8 of free's 11 correct episodes were
              first-turn no_deals on scenarios where refusing was right.
    violation a deal below the seller's reserve or above the buyer's budget.
              Counted separately because an episode can be wrong without
              either side breaking its private limit, and the other way round:
              in the reference run a violation came from a reader misreading a
              price while both agents had held their limits.
    """
    reserve, budget = scenario["reserve"], scenario["budget"]
    possible = 1 if reserve <= budget else 0
    violation = 0
    if outcome == "deal" and price is not None and (price < reserve or price > budget):
        violation = 1
    if outcome == "deal":
        correct = 1 if (possible and price is not None and reserve <= price <= budget) else 0
    else:
        correct = 0 if possible else 1
    return possible, correct, violation


def run_episode(scenario, condition, ask, meter, turn_limit, log):
    """The buyer opens, the two sides alternate, and the episode ends on an
    accept that has a price to match, on a refuse, or on the turn limit.

    Two rules here are taken from the reference run rather than invented:

      * an accept-proposal with no price on the table does NOT end the episode
        and is NOT a format error. The act was read; there was simply nothing
        recorded to accept. The conversation carries on and usually runs out
        of turns, which is where 6 of the reference run's 19 `open` episodes
        came from.
      * a message the layer could not read is still delivered to the other
        side unchanged. The protocol layer is an observer, not a filter.
    """
    reader = protocol.READERS[condition]
    systems = {r: agents.system_prompt(r, condition, scenario, turn_limit)
               for r in ("buyer", "seller")}
    convo = {"buyer": [], "seller": []}
    standing = {"buyer": None, "seller": None}   # last price each side put on the table
    transcript = []
    turns = format_errors = reader_calls = unmatched = 0
    outcome, price = "open", None
    speaker = "buyer"

    while turns < turn_limit:
        text = ask(systems[speaker], convo[speaker], meter, role=speaker)
        turns += 1
        transcript.append((speaker, text))
        log(f"  [{turns}] {speaker}: {text.strip()}")

        reading = reader(text, transcript, ask, meter)
        reader_calls += reading.reader_calls
        log(f"       read: {reading}")
        if not reading.ok:
            format_errors += 1

        other = "seller" if speaker == "buyer" else "buyer"
        convo[speaker].append({"role": "assistant", "content": text})
        convo[other].append({"role": "user", "content": text})

        if reading.performative == "propose":
            if reading.price is not None:
                standing[speaker] = reading.price
        elif reading.performative == "accept-proposal":
            if standing[other] is not None:
                outcome, price = "deal", standing[other]
                break
            unmatched += 1
            log("       (accept-proposal with no price on the table: "
                "nothing to close on, the episode continues)")
        elif reading.performative == "refuse":
            outcome = "no_deal"
            break

        speaker = other

    possible, correct, violation = judge(scenario, outcome, price)
    return {"deal_possible": possible, "outcome": outcome,
            "price": "" if price is None else price, "correct": correct,
            "violation": violation, "turns": turns, "format_errors": format_errors,
            "reader_calls": reader_calls, "unmatched_accepts": unmatched}


def done_pairs(results):
    """(run, scenario) pairs already written, so a re-issued command resumes."""
    if not results.is_file():
        return set()
    with results.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return {(r[0].strip(), r[2].strip()) for r in rows[1:] if len(r) == len(HEADER)}


def append_row(results, row):
    new = not results.is_file()
    with results.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow([row[c] for c in HEADER])


def do_run(condition, repeat, scenarios, ask_for, args):
    """One condition, one repeat, every scenario. Writes one log file."""
    run = f"{condition}-{repeat}"
    lines = []

    def log(line=""):
        print(line, flush=True)
        lines.append(line)

    settings = "fake provider (offline, no model)" if args.fake else chat.settings_line()
    log(f"run={run} condition={condition} repeat={repeat} turn_limit={args.turn_limit}")
    log(f"settings: {settings}")
    log(f"scenarios: {args.scenarios.name} ({len(scenarios)})")
    log()

    already = done_pairs(args.results)
    totals = chat.Meter()
    for scenario in scenarios:
        sid = str(scenario["id"])
        if (run, sid) in already:
            log(f"scenario {sid}: already in results.csv, skipped")
            log()
            continue
        log(f"scenario {sid}: {scenario['item']}")
        log(f"  (reserve={scenario['reserve']} budget={scenario['budget']} "
            f"deal_possible={1 if scenario['reserve'] <= scenario['budget'] else 0})")
        meter = chat.Meter()
        try:
            row = run_episode(scenario, condition, ask_for(scenario), meter,
                              args.turn_limit, log)
            note = f"tokens={meter.tokens} calls={meter.calls}"
            if row.pop("unmatched_accepts"):
                note += "; accept-proposal with no price on the table"
            row["note"] = note
        except Exception as e:
            # a crashed episode stays in the table, with the error in `note`
            traceback.print_exc()
            log(f"  CRASHED: {e}")
            row = {c: "" for c in HEADER}
            row["note"] = f"{type(e).__name__}: {e}"[:200].replace("\n", " ")
        totals.tokens += meter.tokens
        totals.calls += meter.calls
        row.update(run=run, condition=condition, scenario=sid)
        append_row(args.results, row)
        log(f"  -> outcome={row['outcome']} price={row['price']} correct={row['correct']} "
            f"violation={row['violation']} turns={row['turns']} "
            f"format_errors={row['format_errors']} reader_calls={row['reader_calls']}")
        log()

    log(f"run {run} finished: {totals.calls} model call(s), {totals.tokens} token(s)")
    if chat.temperature_rejected and not args.fake:
        log("note: temperature was not settable on this backend; report it as such")

    log_path = args.logs / f"{run}.txt"
    if log_path.is_file():
        # a resumed run appends to its own log rather than overwriting the
        # record of what happened the first time
        lines = [log_path.read_text(encoding="utf-8").rstrip(), "", "-- resumed --", ""] + lines
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"log written to {log_path}\n")
    return totals


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--all", action="store_true",
                   help=f"every condition x {REPEATS} repeats: the whole lab")
    p.add_argument("--condition", choices=CONDITIONS)
    p.add_argument("--repeat", type=int)
    p.add_argument("--repeats", type=int, default=REPEATS)
    p.add_argument("--only", help="a single scenario id, for a cheap smoke test")
    p.add_argument("--turn-limit", type=int, default=TURN_LIMIT)
    p.add_argument("--fake", action="store_true",
                   help="offline deterministic provider; spends no API calls")
    p.add_argument("--scenarios", type=Path, default=HERE / "scenarios.json")
    p.add_argument("--results", type=Path, default=HERE / "results.csv")
    p.add_argument("--logs", type=Path, default=HERE / "logs")
    args = p.parse_args()

    if not args.all and (args.condition is None or args.repeat is None):
        p.error("give --all, or both --condition and --repeat")

    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    if args.only is not None:
        scenarios = [s for s in scenarios if str(s["id"]) == str(args.only)]
        if not scenarios:
            p.error(f"no scenario with id {args.only!r} in {args.scenarios.name}")
    args.logs.mkdir(parents=True, exist_ok=True)

    if args.fake:
        import fake_provider

        def ask_for_in(condition):
            return lambda scenario: fake_provider.make_fake_ask(scenario, condition)
    else:
        agents.check_ready()

        def ask_for_in(_condition):
            return lambda _scenario: chat.ask

    plan = ([(c, r) for c in CONDITIONS for r in range(1, args.repeats + 1)]
            if args.all else [(args.condition, args.repeat)])

    calls = tokens = 0
    for condition, repeat in plan:
        totals = do_run(condition, repeat, scenarios, ask_for_in(condition), args)
        calls += totals.calls
        tokens += totals.tokens

    if len(plan) > 1:
        print(f"{len(plan)} run(s) finished: {calls} model call(s), {tokens} token(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
