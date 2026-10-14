"""Only FORMAT varies between conditions; prompts are frozen before live runs."""

CONDITIONS = ("free", "tagged", "structured")
PERFORMATIVES = ("propose", "accept-proposal", "reject-proposal", "refuse")
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
    " Four acts are available: propose (offer a price), "
    "accept-proposal (agree to the other side's last price, which ends the "
    "negotiation with a deal), reject-proposal (decline the last price and "
    "keep negotiating), and refuse (leave the negotiation for good, no deal)."
)
FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (
        " Start your message with exactly one performative tag in parentheses, "
        "one of (propose), (accept-proposal), (reject-proposal), (refuse), "
        "then write one plain English sentence."
    ),
    "structured": (
        " Reply with exactly one JSON object and nothing else: "
        '{"performative": "propose" | "accept-proposal" | '
        '"reject-proposal" | "refuse", '
        '"content": {"price": <whole number or null>}}.'
    ),
}
READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
    '"refuse", "price": <whole number or null>}.'
)
OPENING_CUE = "The negotiation begins now. Send the buyer's opening message."


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    if role not in ROLE or condition not in FORMAT:
        raise ValueError("unknown role or condition")
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]
