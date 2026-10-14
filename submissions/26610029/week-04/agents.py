"""Week 04 — buyer/seller system prompts, one per condition.

Only the format paragraph changes between conditions; the role description
and the four-act vocabulary are identical across free/tagged/structured so
the comparison isolates the message format, not the negotiation itself.
"""

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit}. Never reveal this "
              "number to the seller. You open the negotiation."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer. "
               "Your private limit: you will not accept less than {limit}. Never "
               "reveal this number to the buyer."),
}

ACTS = (
    "You may only perform one of these four acts per message:\n"
    "- propose: offer a price.\n"
    "- accept-proposal: agree to the other side's last price. This ends the "
    "negotiation with a deal at that price.\n"
    "- reject-proposal: decline the other side's last price and continue "
    "negotiating (you should usually make a counter-proposal).\n"
    "- refuse: leave the negotiation. No deal. Use this only if you believe "
    "no acceptable price is possible.\n"
    f"The negotiation ends after a fixed number of turns if neither side has "
    "accepted or refused."
)

FORMAT = {
    "free": "Write your message as one or two plain English sentences.",
    "tagged": ("Start your message with exactly one performative tag in "
               "parentheses, one of (propose), (accept-proposal), "
               "(reject-proposal), (refuse), then plain English."),
    "structured": ('Reply with exactly one JSON object and nothing else: '
                   '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
                   '"content": {"price": <whole number or null>}}'),
}


def build_system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    return "\n\n".join([
        ROLE[role].format(item=item, limit=limit),
        ACTS,
        FORMAT[condition],
    ])
