"""Private dual confirmation and deterministic limit enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from acl_lab.domain import ChatMessage, ChatRole, Negotiator
from acl_lab.engine import speak
from acl_lab.hybrid_prompts import settlement_request, settlement_system_prompt
from acl_lab.hybrid_protocol import (
    Confirmation,
    ConfirmationFailure,
    ConfirmationResult,
    SettlementDecision,
    parse_confirmation,
    settlement_guards,
)

if TYPE_CHECKING:
    from acl_lab.domain import ModelClient, ModelFailure
    from acl_lab.hybrid_domain import HybridContext


@dataclass(frozen=True, slots=True)
class ConfirmationExecution:
    """One participant's private structured settlement vote."""

    parsed: ConfirmationResult | None
    failure: ModelFailure | None
    calls: int
    tokens: int
    attempts: int


@dataclass(frozen=True, slots=True)
class SettlementExecution:
    """Two private votes plus the deterministic local guard decision."""

    approved: bool
    failure: ModelFailure | None
    calls: int
    tokens: int
    attempts: int
    errors: int
    vetoes: int
    guard_vetoes: int
    evidence: str


def _confirm(
    role: Negotiator,
    context: HybridContext,
    candidate_price: int,
    model: ModelClient,
) -> ConfirmationExecution:
    speaker = speak(
        model,
        settlement_system_prompt(role, context.scenario, candidate_price),
        (ChatMessage(ChatRole.USER, settlement_request(candidate_price)),),
    )
    if speaker.failure is not None:
        return ConfirmationExecution(None, speaker.failure, 1, 0, speaker.attempts)
    parsed = parse_confirmation(speaker.text or "", candidate_price)
    return ConfirmationExecution(parsed, None, 1, speaker.tokens, speaker.attempts)


def _confirmed(result: ConfirmationResult) -> bool:
    match result:
        case Confirmation(decision=decision):
            match decision:
                case SettlementDecision.CONFIRM:
                    return True
                case SettlementDecision.ABORT:
                    return False
                case unreachable:
                    assert_never(unreachable)
        case ConfirmationFailure():
            return False
        case unreachable:
            assert_never(unreachable)


def _decision_label(result: ConfirmationResult) -> str:
    match result:
        case Confirmation(decision=decision):
            return decision.value
        case ConfirmationFailure(reason=reason):
            return reason
        case unreachable:
            assert_never(unreachable)


def _failure_result(
    failure: ModelFailure,
    calls: int,
    tokens: int,
    attempts: int,
    evidence: str,
) -> SettlementExecution:
    return SettlementExecution(
        approved=False,
        failure=failure,
        calls=calls,
        tokens=tokens,
        attempts=attempts,
        errors=0,
        vetoes=0,
        guard_vetoes=0,
        evidence=evidence,
    )


def settle(
    context: HybridContext,
    candidate_price: int,
    model: ModelClient,
) -> SettlementExecution:
    """Require both private votes and both code guards before committing a deal."""
    buyer = _confirm(Negotiator.BUYER, context, candidate_price, model)
    if buyer.failure is not None:
        return _failure_result(
            buyer.failure,
            buyer.calls,
            buyer.tokens,
            buyer.attempts,
            "[settlement] buyer model failure",
        )
    seller = _confirm(Negotiator.SELLER, context, candidate_price, model)
    if seller.failure is not None:
        return _failure_result(
            seller.failure,
            buyer.calls + seller.calls,
            buyer.tokens + seller.tokens,
            buyer.attempts + seller.attempts,
            "[settlement] seller model failure",
        )

    buyer_result = buyer.parsed
    seller_result = seller.parsed
    if buyer_result is None or seller_result is None:
        msg = "unreachable confirmation result"
        raise RuntimeError(msg)
    guards = settlement_guards(context.scenario, candidate_price)
    errors = int(isinstance(buyer_result, ConfirmationFailure)) + int(
        isinstance(seller_result, ConfirmationFailure)
    )
    approved = _confirmed(buyer_result) and _confirmed(seller_result) and guards.approved
    decisions = (
        f"buyer={_decision_label(buyer_result)} seller={_decision_label(seller_result)}"
    )
    buyer_guard = f"buyer_guard={'approve' if guards.buyer_approved else 'reject'}"
    seller_guard = f"seller_guard={'approve' if guards.seller_approved else 'reject'}"
    commit = f"commit={'yes' if approved else 'no'}"
    evidence = " ".join(
        (f"[settlement] candidate={candidate_price}", decisions, buyer_guard, seller_guard, commit)
    )
    return SettlementExecution(
        approved=approved,
        failure=None,
        calls=buyer.calls + seller.calls,
        tokens=buyer.tokens + seller.tokens,
        attempts=buyer.attempts + seller.attempts,
        errors=errors,
        vetoes=int(not approved),
        guard_vetoes=int(not guards.approved),
        evidence=evidence,
    )
