"""Mediator negotiation experiment, kept separate from the Week 04 baseline.

Run with the same API environment as runner.py:
  python mediator_experiment.py --all
  python mediator_experiment.py --all --fake

In this design, buyer and seller send only proposals to a neutral mediator.
The mediator has the private limits, checks offers, and closes at the midpoint
of the overlap as soon as the parties' proposals cross. This deliberately
tests a centralized mechanism: safety comes from the protocol and shared limit
visibility, not from an LLM reader correctly inferring acceptance.
"""
import argparse
import csv
import json
import re
import sys
import traceback
from pathlib import Path

import chat

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.json"
RESULTS = HERE / "mediator_results.csv"
LOGS = HERE / "mediator_logs"
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
TURN_LIMIT = 8
REPEATS = 3
OFFERS = re.compile(r"(?:\$\s*)?(\d{1,5})(?:\s*dollars?)?", re.IGNORECASE)

BUYER_SYSTEM = ("You are a buyer negotiating through a neutral mediator for {item}. "
                "Your private budget is {limit}; never propose above it. Return exactly "
                'one JSON object: {{"offer": integer, "message": "short rationale"}}. '
                "Make a realistic price offer. The mediator will show you the other "
                "side's previous offer and decide when offers overlap.")
SELLER_SYSTEM = ("You are a seller negotiating through a neutral mediator for {item}. "
                 "Your private reserve is {limit}; never propose below it. Return exactly "
                 'one JSON object: {{"offer": integer, "message": "short rationale"}}. '
                 "Make a realistic price offer. The mediator will show you the other "
                 "side's previous offer and decide when offers overlap.")


def parse_offer(text):
    try:
        obj = json.loads(text)
        value = obj.get("offer") if isinstance(obj, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    except (ValueError, TypeError):
        pass
    return None


def fake_ask(scenario):
    """Deterministic offline policy that moves each side toward its limit."""
    counts = {"buyer": 0, "seller": 0}
    def ask(system, messages, meter, role=None):
        role = role or ("buyer" if "buyer" in system.lower() else "seller")
        counts[role] += 1
        n = counts[role]
        if role == "buyer":
            value = min(scenario["budget"], 100 + 55 * n)
        else:
            value = max(scenario["reserve"], 500 - 70 * n)
        return json.dumps({"offer": value, "message": "counteroffer"})
    return ask


def run_episode(scenario, ask, meter, log):
    reserve, budget = scenario["reserve"], scenario["budget"]
    systems = {
        "buyer": BUYER_SYSTEM.format(item=scenario["item"], limit=budget),
        "seller": SELLER_SYSTEM.format(item=scenario["item"], limit=reserve),
    }
    last = {"buyer": None, "seller": None}
    turns = errors = 0
    transcript = {"buyer": [], "seller": []}
    outcome, price = "open", None
    speaker = "buyer"
    while turns < TURN_LIMIT:
        other = "seller" if speaker == "buyer" else "buyer"
        visible = {"your_previous_offer": last[speaker],
                   "other_previous_offer": last[other],
                   "remaining_messages": TURN_LIMIT - turns}
        messages = transcript[speaker] + [{"role": "user", "content": json.dumps(visible)}]
        raw = ask(systems[speaker], messages, meter, role=speaker)
        turns += 1
        offer = parse_offer(raw)
        log(f"  [{turns}] {speaker}: {raw}")
        if offer is None:
            errors += 1
            log("       mediator parse: invalid offer")
        elif speaker == "buyer" and offer > budget:
            errors += 1
            log(f"       mediator reject: {offer} exceeds buyer budget {budget}")
        elif speaker == "seller" and offer < reserve:
            errors += 1
            log(f"       mediator reject: {offer} below seller reserve {reserve}")
        else:
            last[speaker] = offer
            log(f"       mediator accepts offer {offer}")
            if last["buyer"] is not None and last["seller"] is not None and last["buyer"] >= last["seller"]:
                # A deterministic, auditable settlement rule within the feasible interval.
                price = (last["buyer"] + last["seller"]) // 2
                outcome = "deal"
                log(f"       mediator closes at midpoint {price} between buyer {last['buyer']} and seller {last['seller']}")
                break
        other_text = json.dumps({"last_buyer_offer": last["buyer"],
                                 "last_seller_offer": last["seller"],
                                 "mediator_status": "continue"})
        transcript[speaker].append({"role": "assistant", "content": raw})
        transcript[other].append({"role": "user", "content": raw})
        transcript[other].append({"role": "user", "content": other_text})
        speaker = other
    possible = int(reserve <= budget)
    violation = int(outcome == "deal" and (price < reserve or price > budget))
    correct = int((outcome == "deal" and possible == 1 and not violation) or
                  (outcome != "deal" and possible == 0))
    return dict(deal_possible=possible, outcome=outcome,
                price="" if price is None else price, correct=correct,
                violation=violation, turns=turns, format_errors=errors,
                reader_calls=0, note="central mediator sees both private limits")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--repeats", type=int, default=REPEATS)
    p.add_argument("--only", help="one scenario id for a pilot")
    p.add_argument("--fake", action="store_true", help="offline deterministic policy")
    p.add_argument("--results", type=Path, default=RESULTS)
    p.add_argument("--logs", type=Path, default=LOGS)
    args = p.parse_args()
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [s for s in scenarios if str(s["id"]) == args.only]
        if not scenarios:
            p.error(f"unknown scenario id {args.only}")
    plan = range(1, args.repeats + 1) if args.all else [args.repeat]
    args.logs.mkdir(parents=True, exist_ok=True)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    new = not args.results.exists()
    with args.results.open("a", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=HEADER)
        if new:
            writer.writeheader()
        for repeat in plan:
            log_lines = [f"run=mediator-{repeat} turn_limit={TURN_LIMIT}",
                         "settlement=midpoint when buyer offer >= seller offer",
                         "limits=private to parties, visible to mediator"]
            for scenario in scenarios:
                sid = str(scenario["id"])
                run = f"mediator-{repeat}"
                with args.results.open(encoding="utf-8", newline="") as src:
                    existing = list(csv.DictReader(src))
                if any(r["run"] == run and r["scenario"] == sid for r in existing):
                    log_lines.append(f"scenario {sid}: already recorded, skipped")
                    continue
                meter = chat.Meter()
                def log(line=""):
                    print(line, flush=True)
                    log_lines.append(line)
                log(f"scenario {sid}: reserve={scenario['reserve']} budget={scenario['budget']}")
                try:
                    if args.fake:
                        ask = fake_ask(scenario)
                    else:
                        ask = chat.ask
                    row = run_episode(scenario, ask, meter, log)
                except Exception as e:
                    traceback.print_exc()
                    row = {k: "" for k in HEADER}
                    row.update(note=f"{type(e).__name__}: {str(e)[:160]}")
                row.update(run=run, condition="mediator", scenario=sid)
                row["note"] += f"; model_calls={meter.calls} tokens={meter.tokens}"
                writer.writerow(row)
                out.flush()
                log(f"  -> outcome={row['outcome']} price={row['price']} correct={row['correct']} violation={row['violation']} turns={row['turns']}")
            (args.logs / f"mediator-{repeat}.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
