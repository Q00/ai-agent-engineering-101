"""Common roles with one condition-specific format paragraph."""

from __future__ import annotations

from typing import Final, assert_never

from acl_lab.domain import Condition, Negotiator, Scenario

COMMON: Final = (
    " Four acts are available: propose offers one whole-number price; accept-proposal agrees "
    "to the other side's last proposal and ends with a deal; reject-proposal declines the last "
    "proposal and continues; refuse leaves permanently with no deal. The buyer speaks first. "
    "Never reveal your private limit. Never accept a price outside your private limit."
)

READER_SYSTEM: Final = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative":"propose"|"accept-proposal"|"reject-proposal"|"refuse",'
    '"price":<whole number or null>}. Use price only for the price proposed in the last message.'
)


def system_prompt(role: Negotiator, scenario: Scenario, condition: Condition) -> str:
    """Keep role semantics fixed and vary only the final format paragraph."""
    match role:
        case Negotiator.BUYER:
            role_text = (
                f"You are the buyer of {scenario.item}. Your private budget is "
                f"{scenario.budget}; you can pay at most this amount."
            )
        case Negotiator.SELLER:
            role_text = (
                f"You are the seller of {scenario.item}. Your private reserve is "
                f"{scenario.reserve}; you can accept at least this amount."
            )
        case unreachable:
            assert_never(unreachable)

    match condition:
        case Condition.FREE:
            format_text = " Write your message as one or two plain English sentences."
        case Condition.TAGGED:
            format_text = (
                " Start with exactly one tag: (propose), (accept-proposal), "
                "(reject-proposal), or (refuse), followed by one plain English sentence."
            )
        case Condition.STRUCTURED:
            format_text = (
                ' Reply with exactly one JSON object and nothing else: {"performative":'
                '"propose"|"accept-proposal"|"reject-proposal"|"refuse",'
                '"content":{"price":<whole number or null>}}.'
            )
        case unreachable:
            assert_never(unreachable)
    return role_text + COMMON + format_text


def reader_input(transcript: tuple[str, ...]) -> str:
    """Ask the fixed reader to classify only the final visible message."""
    return "Conversation:\n" + "\n".join(transcript)
