"""Every prompt in the experiment, in one place so the report can quote them.

Fixed across the three conditions: the two role prompts (buyer, seller) and
the common act paragraph. Variable: FORMAT[condition], one paragraph appended
to the system prompt, and nothing else. The reader prompts belong to the
protocol layer, not to the agents; the agents never see them.
"""

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

# ------------------------------------------------------------ role prompts

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

# The four acts are the FIPA Communicative Act Library names the harness
# understands. The paragraph names them for every condition; only FORMAT
# decides whether the agent has to write the name down.
COMMON = (
    "Each message you send does exactly one of four things:\n"
    "- propose: name a price you would trade at (a first offer or a counter-offer);\n"
    "- accept-proposal: agree to the price the other side named most recently, "
    "which closes the deal at that price;\n"
    "- reject-proposal: decline the other side's most recent price and keep negotiating, "
    "without naming a new price;\n"
    "- refuse: leave the negotiation; there is no deal.\n"
    "Accept only a price the other side has actually named, and never agree to a price "
    "outside your private limit. If no price inside your limit looks reachable, refuse "
    "rather than cross the limit. The negotiation ends automatically after {max_turns} "
    "messages in total; a negotiation that runs out of turns is a failure for both sides."
)

# ---------------------------------------------------------- format paragraph

FORMAT = {
    "free": (
        "Format: write your message in plain English, one or two sentences, "
        "as you would speak to the other person. No tags, no labels, no JSON."
    ),
    "tagged": (
        "Format: begin your message with exactly one tag in parentheses naming your act: "
        "(propose), (accept-proposal), (reject-proposal) or (refuse). "
        "Then one or two sentences of plain English. "
        "Example: (propose) I could go to 45 dollars for it."
    ),
    "structured": (
        "Format: reply with exactly one JSON object and nothing else, no prose, no code fence: "
        '{"performative": "<propose|accept-proposal|reject-proposal|refuse>", '
        '"content": {"price": <integer or null>}}. '
        "With propose, price is the integer you offer. With accept-proposal, price is the "
        "price you are accepting. With reject-proposal and refuse, price is null."
    ),
}


def system_prompt(role: str, scenario: dict, condition: str, max_turns: int) -> str:
    return "\n\n".join([
        ROLE[role].format(**scenario),
        COMMON.format(max_turns=max_turns),
        FORMAT[condition],
    ])


# ------------------------------------------------------------ reader prompts

# free: the reader labels the whole message. It sees the previous message
# from the other side as context, because "fine, 180 it is" is an acceptance
# only if 180 was the other side's price and a proposal otherwise. It sees
# nothing else: no limits, no history beyond one message.
READER_FREE = (
    "You label messages in a two-party price negotiation between a buyer and a seller. "
    "Decide which one act the message performs:\n"
    "- propose: the speaker names a price at which they would trade (first offer or counter-offer);\n"
    "- accept-proposal: the speaker agrees to the price the other side named most recently, closing the deal;\n"
    "- reject-proposal: the speaker declines the other side's price but keeps negotiating without naming a new price;\n"
    "- refuse: the speaker ends the negotiation with no deal.\n"
    "Reply with exactly one JSON object and nothing else: "
    '{"performative": "<propose|accept-proposal|reject-proposal|refuse>", '
    '"price": <the integer price the speaker names, or null>}.'
)

READER_FREE_INPUT = (
    "Previous message from the {other}: {previous}\n"
    "Message from the {speaker} to label: {text}"
)

# tagged: the tag is read by a regex; the reader is only asked for the number
# inside a propose.
READER_PRICE = (
    "A speaker in a price negotiation is making an offer. Extract the price they propose. "
    'Reply with exactly one JSON object and nothing else: {"price": <integer or null>}.'
)

READER_PRICE_INPUT = "Message: {text}"
