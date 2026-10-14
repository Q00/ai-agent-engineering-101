from dataclasses import dataclass

from agents import agent_message
from model import Meter
from protocol import read_message


TURN_LIMIT = 6


@dataclass
class EpisodeResult:
    outcome: str
    price: int | None
    correct: int
    violation: int
    turns: int
    format_errors: int
    reader_calls: int


def evaluate_outcome(outcome, price, reserve, budget):
    deal_possible = reserve <= budget

    violation = 0
    if outcome == "deal" and price is not None:
        if price < reserve or price > budget:
            violation = 1

    if deal_possible:
        correct = int(
            outcome == "deal"
            and price is not None
            and reserve <= price <= budget
        )
    else:
        correct = int(outcome == "no_deal")

    return correct, violation


def run_episode(scenario, condition, log=print):
    item = scenario["item"]
    reserve = scenario["reserve"]
    budget = scenario["budget"]

    agent_meter = Meter()
    reader_meter = Meter()

    conversation = []
    format_errors = 0
    reader_calls = 0
    last_offer = None
    last_offer_by = None

    outcome = "open"
    deal_price = None

    for turn in range(1, TURN_LIMIT + 1):
        role = "buyer" if turn % 2 == 1 else "seller"
        private_limit = budget if role == "buyer" else reserve

        message = agent_message(
            role=role,
            item=item,
            private_limit=private_limit,
            condition=condition,
            conversation=conversation,
            meter=agent_meter,
            opening=(turn == 1),
        )

        log(f"[message] turn={turn} role={role}: {message}")
        conversation.append(f"{role.upper()}: {message}")

        parsed, reader_raw, calls = read_message(
            condition=condition,
            message=message,
            meter=reader_meter,
        )
        reader_calls += calls

        if parsed is None:
            format_errors += 1
            log(f"[parse] ERROR: {reader_raw!r}")
            continue

        performative = parsed["performative"]
        price = parsed["price"]

        log(
            f"[parse] performative={performative} "
            f"price={price} reader_calls={calls}"
        )

        if performative == "propose":
            if price is None:
                format_errors += 1
                log("[protocol] propose without price")
                continue

            last_offer = price
            last_offer_by = role
            continue

        if performative == "accept-proposal":
            other_role = "seller" if role == "buyer" else "buyer"

            if last_offer is None or last_offer_by != other_role:
                format_errors += 1
                log("[protocol] invalid acceptance: no offer from other side")
                continue

            outcome = "deal"
            deal_price = last_offer
            log(f"[episode] DEAL price={deal_price}")
            break

        if performative == "refuse":
            outcome = "no_deal"
            log("[episode] NO_DEAL by refuse")
            break

        if performative == "reject-proposal":
            continue

    turns = len(conversation)

    if outcome == "open":
        log("[episode] OPEN turn limit reached")

    correct, violation = evaluate_outcome(
        outcome=outcome,
        price=deal_price,
        reserve=reserve,
        budget=budget,
    )

    log(
        f"[result] outcome={outcome} price={deal_price} "
        f"correct={correct} violation={violation} "
        f"turns={turns} format_errors={format_errors} "
        f"reader_calls={reader_calls} "
        f"agent_calls={agent_meter.calls}"
    )

    return EpisodeResult(
        outcome=outcome,
        price=deal_price,
        correct=correct,
        violation=violation,
        turns=turns,
        format_errors=format_errors,
        reader_calls=reader_calls,
    )
