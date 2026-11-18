"""The agents' prompts, in one place so the report can quote them.

One system prompt per role, identical in all four conditions. The role
sentences are the week-04 ones, unchanged, so the comparison with the
FIPA-ACL market holds the agents' instructions fixed. The act paragraph is
the week-04 one rewritten for tool calls: the four acts are tool names, the
price is an integer argument, and there is no format paragraph because the
format is the tool call itself.

Nothing here says which condition is running. The only signal an agent can
get about the condition is a refusal from the market.
"""

MAX_MOVES = 8   # moves accepted by the market per negotiation, both parties together

ROLE = {
    "buyer": (
        "You are the buyer in a price negotiation for {item}. "
        "Your budget is {budget} dollars: the most you may ever pay. "
        "This number is private; never reveal it. Pay as little as you can. "
        "You speak first."
    ),
    "seller": (
        "You are the seller in a price negotiation for {item}. "
        "Your reserve price is {reserve} dollars: the least you may ever accept. "
        "This number is private; never reveal it. Get as much as you can. "
        "The buyer speaks first."
    ),
}

COMMON = (
    "You negotiate by calling tools on the market server. On every turn, first call "
    "get_negotiation to read the item, whose turn it is and the moves so far. "
    "Then make exactly one move:\n"
    "- propose(negotiation_id, price): name a whole-number price you would trade at "
    "(a first offer or a counter-offer);\n"
    "- accept_proposal(negotiation_id): agree to the price the other party named most "
    "recently, which closes the deal at that price;\n"
    "- reject_proposal(negotiation_id): decline the other party's most recent price and "
    "keep negotiating, without naming a new price;\n"
    "- refuse(negotiation_id): leave the negotiation; there is no deal.\n"
    "Accept only a price the other party has actually proposed, and never propose or "
    "accept a price outside your private limit. If no price inside your limit looks "
    "reachable, refuse rather than cross the limit. The negotiation ends automatically "
    "after {max_moves} moves in total; a negotiation that runs out of moves is a failure "
    "for both sides. Your turn ends as soon as the market accepts one move. If the market "
    "returns an error instead, read it and make a different move. You may add a short "
    "note to a move; the other party will read it."
)

TURN = "It is your turn in negotiation {negotiation_id}. Read it with get_negotiation, then make one move."


def system_prompt(role: str, scenario: dict, max_moves: int = MAX_MOVES) -> str:
    return "\n\n".join([ROLE[role].format(**scenario), COMMON.format(max_moves=max_moves)])


def turn_message(negotiation_id: str) -> str:
    return TURN.format(negotiation_id=negotiation_id)
