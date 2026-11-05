"""Frozen prompts: a condition is deliberately not an input to this module."""

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit: you can pay at most {limit}. "
        "Never agree to a price above {limit}."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit: you can accept at least {limit}. "
        "Never agree to a price below {limit}."
    ),
}
COMMON = (
    " Use the market's MCP tools. First read the negotiation with get_negotiation. "
    "Four moves are available: propose (offer a whole-number price), "
    "accept_proposal (agree to the other party's last price and close with a deal), "
    "reject_proposal (decline and keep negotiating), and refuse (leave with no deal). "
    "Make one successful move on this turn. A tool error does not consume your turn; "
    "read the reason and choose another move if needed. "
    "The negotiation has at most 8 turns in total."
)


def system_prompt(role: str, item: str, limit: int) -> str:
    if role not in ROLE or type(limit) is not int or limit < 0:
        raise ValueError("invalid role or private limit")
    return ROLE[role].format(item=item, limit=limit) + COMMON


def turn_prompt(negotiation_id: str) -> str:
    return f"It is your turn in negotiation {negotiation_id}. Read its current state and make one move."
