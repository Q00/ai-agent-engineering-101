"""Week 04 — buyer/seller agents: system prompts per role, message format
per condition (free/tagged/structured). No parsing here yet -- that's the
protocol layer, built next.
"""
from model import Meter, call_conversation

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

FORMAT_INSTRUCTIONS = {
    "free": (
        "Write your reply in plain natural English, one short paragraph. "
        "Do not use any tags, labels, or structured format."
    ),
    "tagged": (
        "Write your reply starting with one performative tag in parentheses, "
        "then plain English. The tag must be exactly one of: (propose), "
        "(accept-proposal), (reject-proposal), (refuse). "
        "Example: (propose) I can build this for 350."
    ),
    "structured": (
        "Reply with exactly one JSON object and nothing else: "
        '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
        '"content": {"price": <integer or null>}}'
    ),
}

BUYER_SYSTEM = (
    "You are a procurement officer for a government ministry, negotiating "
    "the construction of: {item}. Your maximum budget is {budget} (unit: "
    "100M KRW). You must never state this number directly to the other "
    "side. You want the lowest price that still gets the project built. "
    "You open the negotiation. Allowed acts: propose (offer a price), "
    "accept-proposal (agree to the other side's last price, ends the deal), "
    "reject-proposal (decline, keep negotiating), refuse (walk away, no "
    "deal). {format_instruction}"
)

SELLER_SYSTEM = (
    "You are a representative of a construction company, negotiating to "
    "build: {item}. Your reserve price is {reserve} (unit: 100M KRW) -- the "
    "lowest you can accept without a loss. You must never state this number "
    "directly to the other side. You want the highest price you can get. "
    "Allowed acts: propose (offer a price), accept-proposal (agree to the "
    "other side's last price, ends the deal), reject-proposal (decline, "
    "keep negotiating), refuse (walk away, no deal). {format_instruction}"
)


def buyer_system(scenario, condition):
    return BUYER_SYSTEM.format(item=scenario["item"], budget=scenario["budget"],
                                format_instruction=FORMAT_INSTRUCTIONS[condition])


def seller_system(scenario, condition):
    return SELLER_SYSTEM.format(item=scenario["item"], reserve=scenario["reserve"],
                                 format_instruction=FORMAT_INSTRUCTIONS[condition])


if __name__ == "__main__":
    # smallest test: buyer opens, seller replies once, free format
    scenario = {"id": 1, "item": "Regional edge data center construction",
                "reserve": 300, "budget": 380}
    m = Meter()

    opening = call_conversation(
        buyer_system(scenario, "free"),
        [{"role": "user", "content": "Begin the negotiation with your opening move."}], m)
    print(f"[buyer] {opening}")

    reply = call_conversation(
        seller_system(scenario, "free"),
        [{"role": "user", "content": opening}], m)
    print(f"[seller] {reply}")

    print(f"tokens={m.tokens} calls={m.calls}")
