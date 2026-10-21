"""Deterministic message parsing and outcome scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, assert_never

from pydantic import ValidationError

from acl_lab.domain import FrozenModel, Outcome, Performative, Scenario


class StructuredContent(FrozenModel):
    """The only content field used by the structured condition."""

    price: int | None


class StructuredEnvelope(FrozenModel):
    """The structured condition's complete wire message."""

    performative: Performative
    content: StructuredContent


class ReaderEnvelope(FrozenModel):
    """The fixed reader's classification output."""

    performative: Performative
    price: int | None


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """A protocol act usable by the episode state machine."""

    performative: Performative
    price: int | None
    reader_needed: bool = False


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """An unreadable message retained as an observed format error."""

    reason: str


type ParseResult = ParsedMessage | ParseFailure


@dataclass(frozen=True, slots=True)
class Score:
    """Correctness and private-limit violation for a terminal outcome."""

    correct: int
    violation: int


_TAG: Final = re.compile(
    r"^\((?P<performative>propose|accept-proposal|reject-proposal|refuse)\)(?:\s|$)"
)


def parse_structured(text: str) -> ParseResult:
    """Parse exactly one JSON object and require a price on proposals."""
    try:
        envelope = StructuredEnvelope.model_validate_json(text)
    except ValidationError:
        return ParseFailure(reason="structured_invalid_json")
    if envelope.performative is Performative.PROPOSE and envelope.content.price is None:
        return ParseFailure(reason="structured_proposal_without_price")
    return ParsedMessage(
        performative=envelope.performative,
        price=envelope.content.price,
    )


def parse_tagged(text: str) -> ParseResult:
    """Read the leading performative tag without interpreting its prose."""
    match = _TAG.match(text)
    if match is None:
        return ParseFailure(reason="tag_missing_or_invalid")
    performative = Performative(match.group("performative"))
    return ParsedMessage(
        performative=performative,
        price=None,
        reader_needed=performative is Performative.PROPOSE,
    )


def parse_reader(text: str) -> ParseResult:
    """Parse the reader's fixed JSON answer."""
    try:
        envelope = ReaderEnvelope.model_validate_json(text)
    except ValidationError:
        return ParseFailure(reason="reader_invalid_json")
    if envelope.performative is Performative.PROPOSE and envelope.price is None:
        return ParseFailure(reason="reader_proposal_without_price")
    return ParsedMessage(performative=envelope.performative, price=envelope.price)


def score_outcome(scenario: Scenario, outcome: Outcome, price: int | None) -> Score:
    """Apply the preregistered reserve and budget after termination."""
    match outcome:
        case Outcome.DEAL:
            if price is None:
                return Score(correct=0, violation=1)
            violation = int(price < scenario.reserve or price > scenario.budget)
            return Score(
                correct=int(scenario.deal_possible and violation == 0), violation=violation
            )
        case Outcome.NO_DEAL | Outcome.OPEN:
            return Score(correct=int(not scenario.deal_possible), violation=0)
        case unreachable:
            assert_never(unreachable)
