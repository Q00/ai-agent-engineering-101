"""One episode: buyer and seller alternate until someone closes or the turns run out.

This module never branches on the condition. It asks protocol.read() what the
last message was and acts on the answer, so the loop, the scoring and the log
are identical in all three conditions.
"""
from acl import system_prompt
from model import Meter, call_model
from protocol import read

# Turn limit. The lecture skeleton uses 8; 10 leaves room for two or three
# rounds of offer and counter-offer before `open`. It is stated to both agents
# through COMMON -- see the note there on why hiding it did not work.
MAX_TURNS = 10

# The buyer speaks first, so its history is empty and both SDKs reject that.
# One turn seeds it.
#
# A neutral "Begin." was tried first. The buyer then opened with a question --
# "what is your asking price?" -- which none of the four acts covers, the reader
# labelled it refuse or an empty propose, and the episode died at turn 1. The
# README expects that and calls it data, but it spends whole episodes on one
# known artefact, so the opener now asks for an opening offer instead. It is the
# same string in all three conditions.
OPENER = "Begin the negotiation by making your opening offer."


class Episode:
    """One row of results.csv."""

    def __init__(self, scenario, condition):
        self.scenario = scenario["id"]
        self.condition = condition
        self.deal_possible = int(scenario["reserve"] <= scenario["budget"])
        self.outcome = "open"
        self.price = None
        self.violation = 0
        # No deal is the right answer when no deal was possible. A deal
        # overwrites this below. `open` in an impossible scenario stays correct:
        # the README asks for a deal exactly when reserve <= budget, and `open`
        # is not a deal. outcome is recorded too, so this can be re-scored from
        # results.csv without re-running anything.
        self.correct = int(not self.deal_possible)
        self.turns = 0
        self.format_errors = 0
        self.reader_calls = 0
        self.notes = []

    def note(self) -> str:
        return " ".join(self.notes)

    def row(self, run: int) -> dict:
        return {
            "run": run, "condition": self.condition, "scenario": self.scenario,
            "deal_possible": self.deal_possible, "outcome": self.outcome,
            "price": "" if self.price is None else self.price,
            "correct": self.correct, "violation": self.violation,
            "turns": self.turns, "format_errors": self.format_errors,
            "reader_calls": self.reader_calls, "note": self.note(),
        }


def run_episode(scenario, condition, log=print) -> Episode:
    ep = Episode(scenario, condition)
    item = scenario["item"]

    systems = {
        "buyer": system_prompt("buyer", item, scenario["budget"], condition, MAX_TURNS),
        "seller": system_prompt("seller", item, scenario["reserve"], condition, MAX_TURNS),
    }
    # The buyer's history is seeded; the seller's fills up when the buyer speaks.
    history = {"buyer": [{"role": "user", "content": OPENER}], "seller": []}
    transcript = []
    last_price = {"buyer": None, "seller": None}

    agents, reader = Meter(), Meter()
    accept_without_price = 0
    role, other = "buyer", "seller"

    log(f"[scenario {scenario['id']}] {item}  reserve={scenario['reserve']} "
        f"budget={scenario['budget']}  deal_possible={ep.deal_possible}")

    for _ in range(MAX_TURNS):
        text = call_model(systems[role], history[role], agents, log=log)
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append((role, text))
        ep.turns += 1
        log(f"[{role}] {text}")

        perf, price, ok = read(condition, text, transcript, reader, log=log)

        if not ok:
            ep.format_errors += 1          # unreadable, but the message still went out
        elif perf == "propose":
            last_price[role] = price
        elif perf == "accept-proposal":
            agreed = last_price[other]
            if agreed is None:
                # The other side never made a propose the layer could record, so
                # there is no price to close at. The agents think they have a
                # deal; the program cannot see one. The episode keeps going --
                # falling through to the role swap, not `continue`, or the same
                # agent would speak twice.
                log("    [episode] accept-proposal, but no recorded price from "
                    f"{other} -- cannot close")
                accept_without_price += 1
            else:
                ep.outcome, ep.price = "deal", agreed
                break
        elif perf == "refuse":
            ep.outcome = "no_deal"
            break

        role, other = other, role

    if ep.outcome == "deal":
        ep.violation = int(ep.price < scenario["reserve"] or ep.price > scenario["budget"])
        ep.correct = int(ep.deal_possible and not ep.violation)
        # correct only asks whether the price sat inside both limits, so a deal
        # that hands one side the whole ZOPA still scores 1. The split goes in
        # note, where part 4 can quote it. results.csv's header is fixed, so it
        # cannot become a column of its own.
        ep.notes.append(f"buyer_surplus={scenario['budget'] - ep.price} "
                        f"seller_surplus={ep.price - scenario['reserve']}")

    ep.reader_calls = reader.calls
    if accept_without_price:
        ep.notes.append(f"accept-without-price={accept_without_price}")
    ep.notes.append(f"agent_calls={agents.calls} agent_tokens={agents.tokens} "
                    f"reader_tokens={reader.tokens} retries={agents.retries + reader.retries}")

    log(f"[result] outcome={ep.outcome} price={ep.price} correct={ep.correct} "
        f"violation={ep.violation} turns={ep.turns} "
        f"format_errors={ep.format_errors} reader_calls={ep.reader_calls}")
    return ep
