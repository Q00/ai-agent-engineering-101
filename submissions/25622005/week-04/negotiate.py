"""Week 04 — one episode: buyer and seller alternate until an act ends it.

The loop is the same in all three conditions. What changes is read(), which
acl.py picks by condition name.
"""
from dataclasses import dataclass, field

from acl import MAX_TURNS, Meter, call_model, read, system_prompt


@dataclass
class Episode:
    scenario: int
    condition: str
    deal_possible: int
    outcome: str = "open"
    price: object = None            # int, or None when there is no deal
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    note: str = ""
    meter: Meter = field(default_factory=Meter)


def run_episode(scenario: dict, condition: str, log=print) -> Episode:
    ep = Episode(scenario=scenario["id"], condition=condition,
                 deal_possible=int(scenario["reserve"] <= scenario["budget"]))
    limits = {"buyer": scenario["budget"], "seller": scenario["reserve"]}
    systems = {r: system_prompt(r, scenario["item"], limits[r], condition)
               for r in ("buyer", "seller")}
    history = {"buyer": [], "seller": []}
    last_price = {"buyer": None, "seller": None}
    transcript = []
    role, other = "buyer", "seller"

    for _ in range(MAX_TURNS):
        text = call_model(systems[role], history[role], ep.meter, log).strip()
        ep.turns += 1
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append((role, text))
        log(f"[{role}]  {text}")

        before = ep.meter.reader_calls
        perf, price, ok = read(condition, text, transcript, ep.meter, log)
        how = "reader" if ep.meter.reader_calls > before else "parse"
        log(f"  [{how}] {{'performative': {perf!r}, 'price': {price!r}}}"
            if ok else f"  [{how}] unreadable -> format_errors")

        if not ok:
            ep.format_errors += 1            # the message still goes to the other agent
        elif perf == "propose":
            last_price[role] = price
        elif perf == "accept-proposal":
            if last_price[other] is None:
                # nothing to accept as far as this layer knows; the negotiation
                # runs on rather than booking a deal at a price nobody recorded
                log("  [note] accept-proposal with no recorded price, continuing")
            else:
                ep.outcome, ep.price = "deal", last_price[other]
                break
        elif perf == "refuse":
            ep.outcome = "no_deal"
            break

        role, other = other, role

    if ep.outcome == "deal":
        ep.violation = int(ep.price < scenario["reserve"] or ep.price > scenario["budget"])
        ep.correct = int(ep.deal_possible and not ep.violation)
    elif ep.outcome == "no_deal":
        ep.correct = int(not ep.deal_possible)

    ep.reader_calls = ep.meter.reader_calls
    log(f"[result] outcome={ep.outcome} price={ep.price} correct={ep.correct} "
        f"violation={ep.violation} turns={ep.turns} "
        f"format_errors={ep.format_errors} reader_calls={ep.reader_calls}")
    return ep
