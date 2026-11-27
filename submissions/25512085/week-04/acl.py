"""Shared negotiation prompts for the Week 04 message-format experiment."""

from __future__ import annotations


PERFORMATIVES = (
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
)
MAX_TURNS = 8

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit is {limit}: you can pay at most {limit}. "
        "Never accept a price above {limit}. On your first turn, make a price proposal."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit is {limit}: you can accept at least {limit}. "
        "Never accept a price below {limit}."
    ),
}

COMMON = (
    " Four acts are available: propose (offer a whole-number price), "
    "accept-proposal (agree to the other side's last proposed price, which ends "
    "the negotiation with a deal), reject-proposal (decline the last price and "
    "keep negotiating), and refuse (leave the negotiation for good, with no deal). "
    "Use exactly one of these four acts. If you propose, use a whole-number price."
)

# This is the only condition-dependent part of the agent system prompt.
FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (
        " Start your message with exactly one performative tag in parentheses, one of "
        "(propose), (accept-proposal), (reject-proposal), or (refuse), then write "
        "one plain English sentence."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: {"performative": '
        '"propose" | "accept-proposal" | "reject-proposal" | "refuse", '
        '"content": {"price": <whole number or null>}}.'
    ),
}

READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only, using the preceding conversation only to identify "
    "the other side's last proposal. Reply with exactly one JSON object and nothing "
    "else: {\"performative\": \"propose\" | \"accept-proposal\" | "
    "\"reject-proposal\" | \"refuse\", \"price\": <whole number or null>}. "
    "Use a price only when the last message makes a proposal; otherwise use null."
)


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    """Build a role prompt with identical rules except for its message format."""
    if role not in ROLE:
        raise ValueError(f"unknown role: {role}")
    if condition not in FORMAT:
        raise ValueError(f"unknown condition: {condition}")
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]
