"""System prompts for the two parties. Identical across conditions.

The role paragraph is week-04's, word for word. The acts paragraph changed:
in week 04 the model wrote a message that a reader labelled as one of four
acts; here the act is the MCP tool it calls, and the price is an integer
argument. Nothing in these prompts says which condition is running.
"""

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit}. Never agree to a price "
              "above {limit}, and never reveal the number itself. You open the negotiation."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer. "
               "Your private limit: you can accept at least {limit}. Never agree to a price "
               "below {limit}, and never reveal the number itself."),
}

COMMON = (
    " The negotiation is held on a market server that you reach through tools. "
    "Your negotiation id is {negotiation_id}. First call get_negotiation to read the item, "
    "whose turn it is, and the moves so far. Then make exactly one move by calling one of: "
    "propose (offer a whole-number price), accept_proposal (agree to the other side's last "
    "price, which ends the negotiation with a deal), reject_proposal (decline the last price "
    "and keep negotiating), refuse (leave the negotiation for good, no deal). "
    "One move ends your turn; after it, stop.")


def system_prompt(role: str, item: str, limit: int, negotiation_id: str) -> str:
    return ROLE[role].format(item=item, limit=limit) + COMMON.format(negotiation_id=negotiation_id)
