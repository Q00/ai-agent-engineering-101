"""One episode: the buyer opens, the two agents alternate, and the condition's
protocol layer reads every message to decide whether the episode has ended."""
from dataclasses import dataclass, field
from typing import Optional

from acl import OPENING, read, system_prompt

MAX_TURNS = 8


@dataclass
class Episode:
    scenario: int
    deal_possible: int
    outcome: str = "open"
    price: Optional[int] = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    # diagnostics for the note column
    unrecorded_accepts: int = 0   # accept-proposal with no recorded price from the other side
    trailing_text: int = 0        # structured: text after the JSON object
    accept_label_price: Optional[int] = None  # free: price the reader read inside the accept
    notes: list = field(default_factory=list)


def _indent(text: str) -> str:
    return text.replace("\n", "\n    ")


def run_episode(sc: dict, condition: str, llm, log) -> Episode:
    reserve, budget = int(sc["reserve"]), int(sc["budget"])
    ep = Episode(scenario=sc["id"], deal_possible=int(reserve <= budget))
    systems = {"buyer": system_prompt("buyer", sc["item"], budget, condition),
               "seller": system_prompt("seller", sc["item"], reserve, condition)}
    history = {"buyer": [{"role": "user", "content": OPENING}], "seller": []}
    last_price = {"buyer": None, "seller": None}
    transcript = []
    reader_before = llm.meter.reader_calls

    log(f"=== scenario {sc['id']}: {sc['item']}, reserve {reserve}, budget {budget}, "
        f"deal_possible={ep.deal_possible}, condition={condition}")
    log(f"[system buyer] {systems['buyer']}")
    log(f"[system seller] {systems['seller']}")

    role, other = "buyer", "seller"
    for _ in range(MAX_TURNS):
        text = llm.chat(systems[role], history[role])
        ep.turns += 1
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append((role, text))
        log(f"[{role}] {_indent(text)}")

        r = read(condition, text, transcript, llm)
        ep.trailing_text += int(r.trailing)
        log(f"    [read] {r.label}")
        if not r.ok:
            ep.format_errors += 1  # unread, but the message still reaches the other side
        elif r.perf == "propose":
            last_price[role] = r.price
        elif r.perf == "accept-proposal":
            if last_price[other] is None:
                ep.unrecorded_accepts += 1
                log("    [protocol] accept-proposal, but no recorded propose from the other side: not a deal")
            else:
                ep.outcome, ep.price = "deal", last_price[other]
                ep.accept_label_price = r.price
                break
        elif r.perf == "refuse":
            ep.outcome = "no_deal"
            break
        role, other = other, role

    ep.reader_calls = llm.meter.reader_calls - reader_before
    if ep.outcome == "deal":
        ep.violation = int(ep.price < reserve or ep.price > budget)
        ep.correct = int(ep.deal_possible and not ep.violation)
        if ep.accept_label_price is not None and ep.accept_label_price != ep.price:
            ep.notes.append(f"accept_text_price={ep.accept_label_price}")
    else:
        ep.correct = int(not ep.deal_possible and ep.outcome == "no_deal")
    if ep.unrecorded_accepts:
        ep.notes.append(f"unrecorded_accepts={ep.unrecorded_accepts}")
    if ep.trailing_text:
        ep.notes.append(f"trailing_text={ep.trailing_text}")

    log(f"[result] outcome={ep.outcome} price={'' if ep.price is None else ep.price} correct={ep.correct} "
        f"violation={ep.violation} turns={ep.turns} format_errors={ep.format_errors} "
        f"reader_calls={ep.reader_calls}" + (f" ({'; '.join(ep.notes)})" if ep.notes else ""))
    return ep
