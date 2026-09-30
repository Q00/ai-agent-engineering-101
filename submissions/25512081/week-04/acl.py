"""Prompts for the week-04 negotiation lab.

Three conditions differ ONLY in the FORMAT paragraph appended to the system
prompt. ROLE (who you are + your private limit) and COMMON (the four acts) are
identical across conditions, so any metric change is attributable to the format
and the protocol layer that reads it — nothing else.

The role prompt forbids crossing the private limit, so a violation recorded
later comes from the reader misreading a price, not from an agent breaking its
own limit. That separation is the point of the experiment.
"""

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit: you can pay at most {limit}. Never agree to a price "
        "above {limit}, and never reveal this limit number. Open the negotiation "
        "and try to buy as cheaply as you can."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit: you can accept at least {limit}. Never agree to a "
        "price below {limit}, and never reveal this limit number. Try to sell as "
        "expensively as you can."
    ),
}

COMMON = (
    " Four acts are available: propose (offer a specific price), accept-proposal "
    "(agree to the other side's last price, which ends the negotiation with a "
    "deal), reject-proposal (decline the last price and keep negotiating), refuse "
    "(leave the negotiation for good, no deal). Send exactly one act per turn. "
    "Accept only when the other side's last price is acceptable under your limit; "
    "otherwise counter with a propose or reject-proposal. Keep every message to "
    "one short line."
)

FORMAT = {
    "free": (
        " Write your message as one or two plain English sentences."
    ),
    "tagged": (
        " Start your message with exactly one performative tag in parentheses, "
        "one of (propose), (accept-proposal), (reject-proposal), (refuse), then "
        "write one plain English sentence."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: '
        '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
        '"refuse", "content": {"price": <whole number or null>}}.'
    ),
}


def system_prompt(role, item, limit, condition):
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


# The reader is the same model at the same temperature; it labels a message in
# the free condition (whole conversation) and the price of a propose in tagged.
READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a "
    "seller. Label the LAST message only. Reply with exactly one JSON object and "
    "nothing else: {\"performative\": \"propose\" | \"accept-proposal\" | "
    "\"reject-proposal\" | \"refuse\", \"price\": <whole number or null>}. "
    "Use price null for any act that names no price."
)
