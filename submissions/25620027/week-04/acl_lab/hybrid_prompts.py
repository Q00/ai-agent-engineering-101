"""Phase-specific public prompts and private settlement prompts."""

from __future__ import annotations

from typing import assert_never

from acl_lab.domain import Condition, Negotiator, Scenario
from acl_lab.hybrid_domain import PublicPhase

HYBRID_COMMON = (
    " Four public acts are available: propose offers one whole-number price; "
    "accept-proposal requests settlement for the other side's last proposal; "
    "reject-proposal declines the last proposal and continues; refuse leaves permanently "
    "with no deal. The buyer speaks first. Never reveal your private limit. Never accept a "
    "price outside your private limit. A deal is final only after the private settlement "
    "gateway approves both parties. If dialogue continues after acceptance, settlement was "
    "not committed."
)


def public_condition(phase: PublicPhase) -> Condition:
    """Map each public state to its wire-format parser."""
    match phase:
        case PublicPhase.DISCOVERY:
            return Condition.FREE
        case PublicPhase.BARGAINING:
            return Condition.TAGGED
        case unreachable:
            assert_never(unreachable)


def _private_role(role: Negotiator, scenario: Scenario) -> str:
    match role:
        case Negotiator.BUYER:
            return (
                f"You are the buyer of {scenario.item}. Your private budget is "
                f"{scenario.budget}; you can pay at most this amount."
            )
        case Negotiator.SELLER:
            return (
                f"You are the seller of {scenario.item}. Your private reserve is "
                f"{scenario.reserve}; you can accept at least this amount."
            )
        case unreachable:
            assert_never(unreachable)


def hybrid_public_prompt(role: Negotiator, scenario: Scenario, phase: PublicPhase) -> str:
    """Keep role semantics fixed while routing the public message format by state."""
    match phase:
        case PublicPhase.DISCOVERY:
            format_text = " Write your message as one or two plain English sentences."
        case PublicPhase.BARGAINING:
            format_text = (
                " Start with exactly one tag: (propose), (accept-proposal), "
                "(reject-proposal), or (refuse), followed by one plain English sentence."
            )
        case unreachable:
            assert_never(unreachable)
    return _private_role(role, scenario) + HYBRID_COMMON + format_text


def settlement_system_prompt(
    role: Negotiator,
    scenario: Scenario,
    candidate_price: int,
) -> str:
    """Request one private structured vote using only that role's own limit."""
    decision_schema = '{"performative":"confirm-deal"|"abort-deal",'
    price_schema = f'"content":{{"price":{candidate_price}}}}}.'
    wire_format = f"one JSON object and nothing else: {decision_schema}{price_schema}"
    return (
        _private_role(role, scenario)
        + f" The settlement candidate price is {candidate_price}. This is a private gateway "
        + "check and is not shown to the other party. Confirm only if this exact price respects "
        + "your private limit and you intend to commit. Never reveal the limit. Reply with exactly "
        + wire_format
    )


def settlement_request(candidate_price: int) -> str:
    """Create the private gateway's one user message."""
    return f"Confirm or abort settlement at price {candidate_price}."
