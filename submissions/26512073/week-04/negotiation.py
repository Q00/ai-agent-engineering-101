from prompts import make_system_prompt
from reader import MessageReader
from retry import send_with_retry
from tools_shared import Chat, Meter


TURN_LIMIT = 8


def run_episode(scenario, condition, log=print):
    meter = Meter()
    reader = MessageReader(meter, log)

    agents = {
        "buyer": Chat(
            make_system_prompt(
                "buyer", scenario["item"], scenario["budget"], condition
            ),
            meter,
            tools=False,
        ),
        "seller": Chat(
            make_system_prompt(
                "seller", scenario["item"], scenario["reserve"], condition
            ),
            meter,
            tools=False,
        ),
    }

    agents["buyer"].add_user("Begin the negotiation.")

    conversation = []
    last_offer = {"buyer": None, "seller": None}
    outcome = "open"
    price = None
    format_errors = 0

    log(f"[scenario] {scenario}")
    log(f"[condition] {condition}")

    for turn in range(1, TURN_LIMIT + 1):
        speaker = "buyer" if turn % 2 == 1 else "seller"
        other = "seller" if speaker == "buyer" else "buyer"

        # Chat.send() already saves this reply as assistant history.
        reply = send_with_retry(agents[speaker], log)
        text = reply.text
        log(f"[turn {turn}] [{speaker}] {text}")

        conversation.append({"speaker": speaker, "text": text})

        # Deliver the original message even if parsing later fails.
        agents[other].add_user(text)

        parsed = reader.read(condition, conversation)

        if parsed is None:
            format_errors += 1
            log("[format error] Message could not be interpreted.")
            continue

        act = parsed["performative"]

        if act == "propose":
            last_offer[speaker] = parsed["price"]

        elif act == "accept-proposal":
            if last_offer[other] is None:
                format_errors += 1
                log("[format error] Acceptance without a prior offer.")
                continue

            price = last_offer[other]
            outcome = "deal"
            log(f"[deal] price={price}")
            break

        elif act == "refuse":
            outcome = "no_deal"
            log("[no_deal] An agent left the negotiation.")
            break

        # reject-proposal continues to the next turn.

    deal_possible = scenario["reserve"] <= scenario["budget"]
    violation = 0
    correct = 0

    if outcome == "deal":
        within_limits = scenario["reserve"] <= price <= scenario["budget"]
        violation = int(not within_limits)
        correct = int(within_limits)
    elif outcome == "no_deal":
        correct = int(not deal_possible)

    result = {
        "scenario": scenario["id"],
        "deal_possible": int(deal_possible),
        "outcome": outcome,
        "price": price,
        "correct": correct,
        "violation": violation,
        "turns": turn,
        "format_errors": format_errors,
        "reader_calls": reader.reader_calls,
        "note": "",
    }

    log(f"[episode result] {result}")
    log(f"[usage] completed_calls={meter.iters}, tokens={meter.tokens}")
    return result