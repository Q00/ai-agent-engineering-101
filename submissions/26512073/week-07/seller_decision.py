"""The student's seller strategy, before wiring it into A2A."""
import json

from harness import call_harness
from seller_memory import load_recent_results


def decide_opening(item, reserve):
    prompt = (
        "You are a seller opening a negotiation. Do not use tools.\n"
        "The student's strategy is to start around 30 percent above the "
        "reserve. Choose a whole-number asking price at or above the reserve.\n"
        "Return only a JSON object with action set to ask, price (an integer), "
        "and reason (one short sentence).\n"
        "Item, private reserve, and recent local results:\n"
        + json.dumps({
            "item": item,
            "reserve": reserve,
            "recent_results": load_recent_results(item),
        })
    )
    decision = call_harness(prompt)
    price = decision.get("price")
    if (decision.get("action") != "ask" or type(price) is not int
            or price < reserve):
        raise ValueError(f"Invalid opening or price below reserve: {decision}")
    return decision


def decide_offer(item, reserve, offer, countered_at_reserve=False):
    state = {
        "item": item,
        "reserve": reserve,
        "buyer_offer": offer,
        "already_countered_at_reserve": countered_at_reserve,
    }
    prompt = (
        "You are a seller deciding the next negotiation action. Do not use tools.\n"
        "Strategy chosen by the student:\n"
        "- Accept a buyer offer at or above the reserve, at the offered price.\n"
        "- On the first below-reserve offer, counter at exactly the reserve.\n"
        "- If you already countered at the reserve and the buyer again offers "
        "less, reject and end the negotiation.\n"
        "Return only a JSON object with action (accept, counter, or reject), "
        "price (an integer for accept/counter, null for reject), and reason "
        "(one short sentence).\n"
        "Negotiation state:\n" + json.dumps(state)
    )
    decision = call_harness(prompt)
    action, price = decision.get("action"), decision.get("price")
    if action not in {"accept", "counter", "reject"}:
        raise ValueError(f"Unknown seller action: {decision}")
    # Code enforces valid prices; the harness still selects the action.
    if action == "reject":
        if price is not None:
            raise ValueError("A rejection must have a null price")
    elif type(price) is not int or price < reserve:
        raise ValueError(f"Seller price must be an integer >= reserve: {decision}")
    elif action == "accept" and price != offer:
        raise ValueError("Acceptance must use the buyer's actual offer")
    return decision


if __name__ == "__main__":
    # Three separate calls: these are real model decisions, not stub replies.
    for offer, countered, expected in [(80, False, "counter"),
                                      (90, True, "reject"),
                                      (100, True, "accept")]:
        answer = decide_offer("lamp", 100, offer, countered)
        print(json.dumps({"offer": offer, "countered": countered,
                          "expected": expected, "decision": answer}), flush=True)
        if answer["action"] != expected:
            raise RuntimeError("Model decision did not follow the chosen strategy")
