"""Prompt definitions for the week-04 negotiation experiment.

The role and protocol text is shared by every condition.  Only the final
format paragraph changes, so differences between conditions can be attributed
to message representation rather than to different negotiation instructions.
"""

from __future__ import annotations


ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating its price with the seller. "
        "Your private budget is {limit}; never agree to or propose a price "
        "above {limit}. Do not reveal that this number is your private limit. "
        "You speak first. Begin with a concrete whole-number offer."
    ),
    "seller": (
        "You are the seller of {item}, negotiating its price with the buyer. "
        "Your private reserve price is {limit}; never agree to or propose a "
        "price below {limit}. Do not reveal that this number is your private "
        "limit."
    ),
}


COMMON = (
    " Four communicative acts are available: propose offers a concrete price; "
    "accept-proposal agrees to the other party's most recent proposed price "
    "and ends the negotiation with a deal; reject-proposal declines that "
    "proposal but keeps negotiating; refuse leaves the negotiation permanently "
    "with no deal. Respond to the conversation so far using exactly one act. "
    "Only use accept-proposal when the other party has already proposed a "
    "price that respects your private limit. Use propose when making a new "
    "price offer, including a counteroffer. Keep the response concise."
)


FORMAT = {
    "free": (
        " Write one or two plain English sentences. Do not include an explicit "
        "performative tag, JSON, or Markdown."
    ),
    "tagged": (
        " Start with exactly one of these tags: (propose), "
        "(accept-proposal), (reject-proposal), or (refuse). Follow the tag "
        "with one plain English sentence. A propose sentence must contain "
        "exactly one whole-number price. Do not use JSON or Markdown."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: '
        '{"performative":"propose|accept-proposal|reject-proposal|refuse",'
        '"content":{"price":<whole number or null>}}. Use a whole-number '
        'price only for propose; use null for every other performative. Do not '
        'use a Markdown code fence.'
    ),
}


READER_SYSTEM = (
    "You are a protocol reader observing a price negotiation between a buyer "
    "and a seller. Label only the final message in the supplied transcript. "
    "Choose exactly one performative: propose, accept-proposal, "
    "reject-proposal, or refuse. For propose, extract the whole-number price "
    "that the speaker offers; for every other performative, use null. A "
    "counteroffer is propose even when it also rejects an earlier price. Reply "
    "with exactly one JSON object and nothing else: "
    '{"performative":"propose|accept-proposal|reject-proposal|refuse",'
    '"price":<whole number or null>}.'
)


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    """Build a role prompt whose only condition-specific text is FORMAT."""

    if role not in ROLE:
        raise ValueError(f"unknown role: {role}")
    if condition not in FORMAT:
        raise ValueError(f"unknown condition: {condition}")
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]
