"""Week-05: the prompts, in one place so it is plain they do not depend on the condition.

The role sentences are week-04's verbatim. The MCP paragraph only says how the
negotiation is conducted; it says nothing about notices or about the market
refusing moves, so the only signal of the condition an agent can get is a
refusal from the server.
"""

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit: you can pay at most {limit}. "
        "Never agree to a price above {limit}. You do not know the seller's limit."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit: you can accept at least {limit}. "
        "Never agree to a price below {limit}. You do not know the buyer's limit."
    ),
}

# week-04's acl.COMMON with the act names changed to the tool names; nothing
# about a move limit, which week-04 did not mention either.
MARKET = (
    "You negotiate through the market's tools. Four moves are available: "
    "propose (offer a price), accept_proposal (agree to the other side's last price, "
    "which ends the negotiation with a deal), reject_proposal (decline the last price "
    "and keep negotiating), refuse (leave the negotiation for good, no deal). "
    "Make exactly one move per turn, and give any price as a whole number."
)

TURN = (
    "Negotiation {negotiation_id}: it is your turn. Call get_negotiation to read it, "
    "then make exactly one move with propose, accept_proposal, reject_proposal, or refuse."
)


def system_prompt(role: str, item: str, limit: int) -> str:
    """The system prompt for one party; the same in every condition."""
    return ROLE[role].format(item=item, limit=limit) + "\n\n" + MARKET


def user_message(negotiation_id: str) -> str:
    """The message that starts one turn (one host run)."""
    return TURN.format(negotiation_id=negotiation_id)
