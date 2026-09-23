"""Structured settlement messages and deterministic private-limit guards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from pydantic import ValidationError

from acl_lab.domain import FrozenModel, Scenario


@unique
class SettlementDecision(StrEnum):
    """The only internal decisions accepted by the settlement gateway."""

    CONFIRM = "confirm-deal"
    ABORT = "abort-deal"


class SettlementContent(FrozenModel):
    """The candidate price repeated by one private confirmation."""

    price: int


class SettlementEnvelope(FrozenModel):
    """Exact structured response accepted by the settlement gateway."""

    performative: SettlementDecision
    content: SettlementContent


@dataclass(frozen=True, slots=True)
class Confirmation:
    """A parsed private settlement response for the exact candidate price."""

    decision: SettlementDecision
    price: int


@dataclass(frozen=True, slots=True)
class ConfirmationFailure:
    """An internal settlement response that cannot authorize a commit."""

    reason: str


type ConfirmationResult = Confirmation | ConfirmationFailure


@dataclass(frozen=True, slots=True)
class SettlementGuards:
    """Independent buyer and seller limit decisions without limit disclosure."""

    buyer_approved: bool
    seller_approved: bool

    @property
    def approved(self) -> bool:
        """Return whether both deterministic guards approve the candidate."""
        return self.buyer_approved and self.seller_approved


def parse_confirmation(text: str, candidate_price: int) -> ConfirmationResult:
    """Parse an exact settlement JSON response for one frozen candidate price."""
    try:
        envelope = SettlementEnvelope.model_validate_json(text)
    except ValidationError:
        return ConfirmationFailure(reason="settlement_invalid_json")
    if envelope.content.price != candidate_price:
        return ConfirmationFailure(reason="settlement_price_mismatch")
    return Confirmation(envelope.performative, envelope.content.price)


def settlement_guards(scenario: Scenario, candidate_price: int) -> SettlementGuards:
    """Evaluate each participant's private limit locally and deterministically."""
    return SettlementGuards(
        buyer_approved=candidate_price <= scenario.budget,
        seller_approved=candidate_price >= scenario.reserve,
    )

