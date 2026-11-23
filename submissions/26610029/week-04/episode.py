"""Week 04 — one negotiation episode: buyer opens, four acts, turn limit."""
from dataclasses import dataclass, field

from agents import build_system_prompt
from reader import read_message, ReaderStats
from tools_shared import Chat, Meter

MAX_TURNS = 8


@dataclass
class EpisodeResult:
    scenario: str
    deal_possible: int
    outcome: str = "open"
    price: int = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    note: str = ""


def run_episode(scenario: dict, condition: str, log=print) -> EpisodeResult:
    reserve, budget, item = scenario["reserve"], scenario["budget"], scenario["item"]
    deal_possible = 1 if reserve <= budget else 0
    r = EpisodeResult(scenario=scenario["id"], deal_possible=deal_possible)

    meter = Meter()
    stats = ReaderStats()
    buyer = Chat(build_system_prompt("buyer", item, budget, condition), meter)
    seller = Chat(build_system_prompt("seller", item, reserve, condition), meter)
    chats = {"buyer": buyer, "seller": seller}

    buyer.add_user("Begin the negotiation now.")
    last_proposal_price = None
    speaker_name = "buyer"
    transcript = []  # plain [role] text lines, for the free-condition reader's context

    for turn in range(MAX_TURNS):
        r.turns = turn + 1
        speaker = chats[speaker_name]
        listener_name = "seller" if speaker_name == "buyer" else "buyer"
        reply = speaker.send()
        log(f"[{speaker_name}] {reply.text.strip()}")

        act = read_message(reply.text, condition, meter, stats, history=transcript)
        transcript.append(f"[{speaker_name}] {reply.text.strip()}")
        r.reader_calls = stats.calls
        log(f"  [reader] {act}")
        if act is None:
            r.format_errors += 1
        else:
            performative = act["performative"]
            if performative == "propose" and act.get("price") is not None:
                last_proposal_price = act["price"]
            elif performative == "accept-proposal":
                r.outcome = "deal"
                r.price = last_proposal_price
                break
            elif performative == "refuse":
                r.outcome = "no_deal"
                break
            # reject-proposal: no state change, negotiation continues

        # feed this turn's raw message to the other agent as their next input
        chats[listener_name].add_user(reply.text)
        speaker_name = listener_name
    else:
        r.outcome = "open"

    if r.outcome == "deal" and r.price is not None:
        r.violation = 1 if (r.price < reserve or r.price > budget) else 0
        r.correct = 1 if (deal_possible and reserve <= r.price <= budget) else 0
    else:
        # no deal (refuse or turn limit): correct iff a deal was never possible
        r.correct = 1 if not deal_possible else 0

    log(f"[episode] scenario={r.scenario} outcome={r.outcome} price={r.price} "
        f"correct={r.correct} violation={r.violation} turns={r.turns} "
        f"format_errors={r.format_errors} reader_calls={r.reader_calls}")
    return r
