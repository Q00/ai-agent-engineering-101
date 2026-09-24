"""One buyer-seller negotiation episode: turn-taking, protocol parsing,
and outcome scoring."""
from model import call_model
from protocol import build_system_prompt, parse_message

MAX_TURNS = 20  # messages exchanged before an unresolved episode is "open"


def run_episode(scenario: dict, condition: str, log=print) -> dict:
    buyer_system = build_system_prompt("buyer", scenario, condition)
    seller_system = build_system_prompt("seller", scenario, condition)
    buyer_history, seller_history = [], []

    turns = 0
    format_errors = 0
    reader_calls = 0
    last_price = None
    outcome = "open"
    deal_price = None
    notes = []
    speaker = "buyer"

    log(f"=== scenario {scenario['id']} ({scenario['item']}) "
        f"reserve={scenario['reserve']} budget={scenario['budget']} condition={condition} ===")

    for turn in range(1, MAX_TURNS + 1):
        system = buyer_system if speaker == "buyer" else seller_system
        history = buyer_history if speaker == "buyer" else seller_history
        # the buyer opens; the Messages API needs at least one message, so
        # seed the very first call with a kickoff instruction (not stored
        # in either transcript -- it is not a negotiation move itself).
        call_messages = history or [{"role": "user",
                                      "content": "Begin the negotiation with your opening message to the seller."}]
        text = call_model(system, call_messages)
        if not text.strip():
            text = call_model(system, call_messages)  # one retry: the API rejects empty content
        if not text.strip():
            format_errors += 1
            text = "(empty response)"
            log(f"[{turn}] {speaker}: <empty response from model, substituted>")

        buyer_history_role, seller_history_role = (
            ("assistant", "user") if speaker == "buyer" else ("user", "assistant")
        )
        buyer_history.append({"role": buyer_history_role, "content": text})
        seller_history.append({"role": seller_history_role, "content": text})
        turns += 1
        if text != "(empty response)":
            log(f"[{turn}] {speaker}: {text}")

        parsed = parse_message(condition, text)
        reader_calls += parsed.reader_calls

        if parsed.format_error:
            format_errors += 1
            log("    parse: FORMAT ERROR (message ignored, treated as reject-proposal)")
        else:
            log(f"    parse: performative={parsed.performative} price={parsed.price}")

            if parsed.performative == "propose" and parsed.price is not None:
                last_price = parsed.price
            elif parsed.performative == "accept-proposal":
                if last_price is not None:
                    outcome, deal_price = "deal", last_price
                    log(f"    -> DEAL at {deal_price}")
                    break
                notes.append(f"turn {turn}: accept-proposal with no price on the table, ignored")
                log("    -> accept-proposal with no price on the table, ignored")
            elif parsed.performative == "refuse":
                outcome = "no_deal"
                log("    -> REFUSE, no deal")
                break

        speaker = "seller" if speaker == "buyer" else "buyer"

    deal_possible = scenario["reserve"] <= scenario["budget"]

    if outcome == "deal":
        violation = int(deal_price < scenario["reserve"] or deal_price > scenario["budget"])
        correct = int(deal_possible and not violation)
    else:
        violation = 0
        correct = int(not deal_possible)

    log(f"=== outcome={outcome} price={deal_price} correct={correct} violation={violation} "
        f"turns={turns} format_errors={format_errors} reader_calls={reader_calls} ===\n")

    return {
        "scenario": scenario["id"],
        "deal_possible": int(deal_possible),
        "outcome": outcome,
        "price": deal_price,
        "correct": correct,
        "violation": violation,
        "turns": turns,
        "format_errors": format_errors,
        "reader_calls": reader_calls,
        "note": "; ".join(notes),
    }
