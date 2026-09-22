"""Prompts for the three message formats, and the observer that reads them.

The role paragraph, the four acts, and the reader prompt are the same in every
condition. Only `FORMAT[condition]`, appended last, differs, so the message
format is the one independent variable.

The text is the week-04 slide's listing. The slide elides four passages with
an ellipsis; each is completed here with the least that makes the task
well-posed, and REPORT.md lists the completions:

  ROLE      how much the agent knows about the other side, kept symmetric.
  COMMON    one act per message and whole-number prices, which the results
            contract needs.
  READER    what `price` refers to.

Nothing here tells an agent to hide or reveal its limit. The reference run
shows agents stating the limit out loud, and a prompt that forbade it would
change what the violation count measures.
"""

ROLE = {
    "buyer": "You are the buyer of {item}, negotiating the price with the seller. "
    "Your private limit: you can pay at most {limit}. Never agree to a "
    "price above {limit}. You do not know the seller's limit.",
    "seller": "You are the seller of {item}, negotiating the price with the buyer. "
    "Your private limit: you can accept at least {limit}. Never agree to a "
    "price below {limit}. You do not know the buyer's limit.",
}

COMMON = (
    " Four acts are available: propose (offer a price), accept-proposal (agree "
    "to the other side's last price, which ends the negotiation with a deal), "
    "reject-proposal (decline the last price and keep negotiating), refuse "
    "(leave the negotiation for good, no deal). Send exactly one act per "
    "message, and give any price as a whole number."
)

FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": " Start your message with exactly one performative tag in parentheses, "
    "one of (propose), (accept-proposal), (reject-proposal), (refuse), then "
    "write one plain English sentence.",
    "structured": " Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" '
    '| "refuse", "content": {"price": <whole number or null>}}.',
}

# The buyer opens, so its first turn has no message from the other side. This
# neutral trigger stands in for one and is identical in all three conditions.
OPENING = "Begin the negotiation."

READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and "
    "a seller. Label the LAST message only. Reply with exactly one JSON "
    'object and nothing else: {"performative": "propose" | '
    '"accept-proposal" | "reject-proposal" | "refuse", "price": <whole '
    "number or null>}. Use the whole number the last message itself "
    "names, or null when it names none."
)

PERFORMATIVES = ("propose", "accept-proposal", "reject-proposal", "refuse")


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    """The full system prompt for one agent in one condition."""
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]
