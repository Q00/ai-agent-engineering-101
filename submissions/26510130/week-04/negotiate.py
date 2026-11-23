"""Week 04 — the two agents and one episode of the negotiation.

Buyer opens. The two sides alternate until someone accepts (deal), someone
refuses (no_deal), or MAX_TURNS messages have been sent (open).

A private limit is never written into the other side's prompt and never into
the transcript: the seller alone knows its reserve, the buyer alone its budget.
The harness knows both, but only to score the episode afterwards.
"""
from dataclasses import dataclass, field

import acl

ROLE = {
    "buyer": (
        "You are the buyer in a price negotiation for {item}. Your budget is "
        "{limit} and you must never agree to pay more than that. You want to pay "
        "as little as you can. You speak only to the seller, one message at a "
        "time, and you never reveal your budget.\n"
        "The four speech acts you may use are: propose (offer a price), "
        "accept-proposal (agree to the seller's last price and close the deal), "
        "reject-proposal (turn down their price but keep negotiating), and "
        "refuse (walk away for good).\n"
        "A counter-offer is a propose, never a reject-proposal: reject-proposal "
        "turns a price down and names none of its own. Only a propose puts a "
        "price on the table that the other side can accept.\n{fmt}"
    ),
    "seller": (
        "You are the seller in a price negotiation for {item}. Your reserve "
        "price is {limit} and you must never agree to sell below it. You want to "
        "get as much as you can. You speak only to the buyer, one message at a "
        "time, and you never reveal your reserve price.\n"
        "The four speech acts you may use are: propose (offer a price), "
        "accept-proposal (agree to the buyer's last price and close the deal), "
        "reject-proposal (turn down their price but keep negotiating), and "
        "refuse (walk away for good).\n"
        "A counter-offer is a propose, never a reject-proposal: reject-proposal "
        "turns a price down and names none of its own. Only a propose puts a "
        "price on the table that the other side can accept.\n{fmt}"
    ),
}


@dataclass
class Episode:
    outcome: str = "open"           # deal | no_deal | open
    price: object = None            # int when outcome == deal
    turns: int = 0
    format_errors: int = 0
    transcript: list = field(default_factory=list)


def system_for(role: str, scenario: dict, condition: str) -> str:
    limit = scenario["reserve"] if role == "seller" else scenario["budget"]
    return ROLE[role].format(item=scenario["item"], limit=limit,
                             fmt=acl.FORMAT[condition])


def run_episode(scenario: dict, condition: str, meter: acl.Meter, log=print) -> Episode:
    read = acl.READERS[condition]
    systems = {r: system_for(r, scenario, condition) for r in ("buyer", "seller")}
    ep = Episode()
    history = {"buyer": [], "seller": []}       # what each side has seen, as text
    last_price = {"buyer": None, "seller": None}
    speaker = "buyer"

    log(f"  [episode {scenario['id']}] item={scenario['item']} "
        f"reserve={scenario['reserve']} budget={scenario['budget']} "
        f"deal_possible={int(scenario['reserve'] <= scenario['budget'])}")

    for turn in range(acl.MAX_TURNS):
        other = "seller" if speaker == "buyer" else "buyer"
        if history[speaker]:
            convo = "\n".join(history[speaker])
            user = f"The conversation so far:\n{convo}\n\nYour reply:"
        else:
            user = ("You speak first. Open the negotiation with the seller."
                    if speaker == "buyer" else "Your reply:")

        text = acl.call_model(systems[speaker], user, meter)
        ep.turns += 1
        log(f"    [{speaker}] {text.strip()[:220]}")
        history["buyer"].append(f"{speaker}: {text.strip()}")
        history["seller"].append(f"{speaker}: {text.strip()}")
        ep.transcript.append((speaker, text.strip()))

        perf, price, ok = read(text, meter, log)
        if not ok:
            # A message the protocol layer cannot read is counted and the
            # episode continues -- the other side still saw the words.
            ep.format_errors += 1
            speaker = other
            continue

        if perf == "propose" and price is not None:
            last_price[speaker] = price
        elif perf == "accept-proposal":
            # accepting means accepting the other side's last proposal
            deal = last_price[other]
            if deal is None:
                log("      [protocol] accept with no standing proposal -> not a deal")
                ep.format_errors += 1
                speaker = other
                continue
            ep.outcome, ep.price = "deal", deal
            log(f"    [outcome] deal at {deal}")
            return ep
        elif perf == "refuse":
            ep.outcome = "no_deal"
            log("    [outcome] no_deal (refuse)")
            return ep

        speaker = other

    log(f"    [outcome] open (turn limit {acl.MAX_TURNS})")
    return ep


def score(ep: Episode, scenario: dict) -> tuple:
    """Returns (correct, violation), both 0/1.

    correct: a deal happened exactly when one was possible, and its price sits
             inside both private limits.
    violation: a deal below the seller's reserve or above the buyer's budget.
    """
    reserve, budget = scenario["reserve"], scenario["budget"]
    possible = reserve <= budget
    if ep.outcome == "deal":
        inside = reserve <= ep.price <= budget
        return int(possible and inside), int(not inside)
    # no deal: correct exactly when no deal was possible. `open` is never
    # correct -- running out of turns is not the same as deciding there is no
    # deal, and scoring it as correct would reward the turn limit.
    return int(ep.outcome == "no_deal" and not possible), 0
