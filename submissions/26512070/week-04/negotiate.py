"""One episode: the buyer opens, the two agents alternate, the protocol layer
reads every message, and the episode ends on accept-proposal, refuse, or the
turn limit.

Each agent sees the negotiation as a chat: its own messages as `assistant`,
the other side's raw text as `user`. The other side's text is forwarded as
written, in every condition -- the protocol layer's reading decides the
episode's state, not what the other agent is shown.
"""
from llm import Meter, chat
import protocol

TURN_LIMIT = 10  # messages in total, both sides; reaching it ends as `open`

# The buyer has nothing to answer on turn one. This line is identical in every
# condition, so it is part of the setup, not of the format.
OPENER = "The seller is listening. You speak first."

ROLE = {
    "buyer": (
        "You are the buyer in a one-on-one price negotiation for {item}. "
        "Your budget is ${budget}: you must never pay more than that. "
        "Your budget is private; do not state it. Pay as little as you can. "
        "If you become sure no acceptable price exists, you may leave. "
        "The negotiation ends after at most {limit} messages in total."
    ),
    "seller": (
        "You are the seller in a one-on-one price negotiation for {item}. "
        "Your reserve price is ${reserve}: you must never sell for less than that. "
        "Your reserve price is private; do not state it. Sell as high as you can. "
        "If you become sure no acceptable price exists, you may leave. "
        "The negotiation ends after at most {limit} messages in total."
    ),
}


def system_prompt(role, scenario, condition):
    return (ROLE[role].format(limit=TURN_LIMIT, **scenario)
            + "\n\n" + protocol.FORMATS[condition])


def judge(scenario, outcome, price):
    """(deal_possible, correct, violation) for one finished episode.

    correct: a deal exactly when reserve <= budget, at a price inside both
    limits. When no deal is possible, both `no_deal` and `open` count as
    correct -- nothing was sold. When a deal is possible, `open` is not.
    """
    lo, hi = scenario["reserve"], scenario["budget"]
    possible = lo <= hi
    deal = outcome == "deal"
    violation = deal and not (lo <= price <= hi)
    correct = (deal and not violation) if possible else not deal
    return int(possible), int(correct), int(violation)


def episode(scenario, condition, log, model=chat):
    """Run one negotiation. Returns the result fields of one results.csv row."""
    agents, reader = Meter(), Meter()
    systems = {r: system_prompt(r, scenario, condition) for r in ROLE}
    views = {"buyer": [{"role": "user", "content": OPENER}], "seller": []}
    last_offer = {"buyer": None, "seller": None}
    format_errors = reader_calls = 0
    notes = []
    outcome, price = "open", None

    speaker = "buyer"
    for turn in range(1, TURN_LIMIT + 1):
        other = "seller" if speaker == "buyer" else "buyer"
        text = model(systems[speaker], views[speaker], agents, log).strip()
        views[speaker].append({"role": "assistant", "content": text})
        views[other].append({"role": "user", "content": text})

        got = protocol.read(condition, text, reader, model=model, log=log)
        reader_calls += got["reader_calls"]
        act, error = got["performative"], got["error"]
        if act == "accept-proposal" and last_offer[other] is None:
            error = f"accept-proposal with no {other} offer to accept"
        if got.get("note"):
            notes.append(f"t{turn} {got['note']}")

        log(f"  t{turn:02d} {speaker:6s}| {text}")
        if got["reader_raw"] is not None:
            log(f"        reader| {got['reader_raw'].strip()}")
        if error:
            format_errors += 1
            log(f"        parse | FORMAT ERROR: {error}")
        else:
            log(f"        parse | {act}" + (f" price={got['price']}" if got["price"] is not None else ""))

        if not error:
            if act == "propose":
                last_offer[speaker] = got["price"]
            elif act == "accept-proposal":
                outcome, price = "deal", last_offer[other]
                break
            elif act == "refuse":
                outcome = "no_deal"
                break
        speaker = other

    possible, correct, violation = judge(scenario, outcome, price)
    return {
        "deal_possible": possible, "outcome": outcome,
        "price": "" if price is None else price,
        "correct": correct, "violation": violation, "turns": turn,
        "format_errors": format_errors, "reader_calls": reader_calls,
        "note": "; ".join(notes),
        "agent_calls": agents.calls, "tokens": agents.tokens + reader.tokens,
    }
