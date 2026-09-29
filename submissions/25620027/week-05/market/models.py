"""Typed domain objects for the Mirror Market experiment."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, NewType, assert_never

from pydantic import BaseModel, ConfigDict, Field

NegotiationId = NewType("NegotiationId", str)


class Role(StrEnum):
    """A party in a bilateral negotiation."""

    BUYER = "buyer"
    SELLER = "seller"

    def other(self) -> Role:
        """Return the opposing role."""
        match self:
            case Role.BUYER:
                return Role.SELLER
            case Role.SELLER:
                return Role.BUYER
            case unreachable:
                assert_never(unreachable)


class Condition(StrEnum):
    """Experimental placement of the limit and injected notice."""

    PROMPT = "prompt"
    SERVER = "server"
    PROMPT_INJECT = "prompt_inject"
    SERVER_INJECT = "server_inject"

    @property
    def enforces_limit(self) -> bool:
        """Return whether the server blocks a caller's violating move."""
        return self in {Condition.SERVER, Condition.SERVER_INJECT}

    @property
    def injects_notice(self) -> bool:
        """Return whether seller proposals carry the fixed buyer notice."""
        return self in {Condition.PROMPT_INJECT, Condition.SERVER_INJECT}


class Status(StrEnum):
    """Negotiation terminal state."""

    OPEN = "open"
    DEAL = "deal"
    NO_DEAL = "no_deal"


class Action(StrEnum):
    """Observable MCP actions and the runner's pass action."""

    GET = "get_negotiation"
    PROPOSE = "propose"
    ACCEPT = "accept_proposal"
    REJECT = "reject_proposal"
    REFUSE = "refuse"
    ADVANCE = "runner_pass"


class EventType(StrEnum):
    """Append-only evidence events."""

    NOTICE_RENDERED = "NOTICE_RENDERED"
    CALL_ATTEMPTED = "CALL_ATTEMPTED"
    CALL_REFUSED = "CALL_REFUSED"
    MOVE_COMMITTED = "MOVE_COMMITTED"
    EPISODE_CLOSED = "EPISODE_CLOSED"
    TURN_PASSED = "TURN_PASSED"


class FrozenModel(BaseModel):
    """Immutable base for experiment values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class Scenario(FrozenModel):
    """One preregistered buyer/seller price boundary."""

    id: int | str
    item: str
    reserve: int = Field(ge=0)
    budget: int = Field(ge=0)


class Grant(FrozenModel):
    """Claims signed into one party's bearer token."""

    subject: str
    role: Role
    negotiation_id: NegotiationId
    limit: int = Field(ge=0)
    condition: Condition
    policy_id: str
    nonce: str


class Move(FrozenModel):
    """A move that passed the market's policy boundary."""

    index: int
    role: Role
    action: Action
    price: int | None = None


class MoveView(FrozenModel):
    """Caller-specific rendering of a committed move."""

    index: int
    role: Role
    action: Action
    price: int | None
    display: str


class Negotiation(FrozenModel):
    """Immutable snapshot owned by the market store."""

    negotiation_id: NegotiationId
    scenario: Scenario
    condition: Condition
    status: Status = Status.OPEN
    turn: Role = Role.BUYER
    moves: tuple[Move, ...] = ()
    price: int | None = None
    attempted_violations: int = 0
    refused_calls: int = 0
    turns: int = 0
    tool_calls: int = 0
    pending_refusals: int = 0
    recovered_refusals: int = 0


class NegotiationView(FrozenModel):
    """The tool result visible to one authenticated party."""

    negotiation_id: NegotiationId
    item: str
    caller_role: Role
    turn: Role
    status: Status
    moves: tuple[MoveView, ...]


class EpisodeSummary(FrozenModel):
    """Metrics consumed by the runner and report."""

    negotiation_id: NegotiationId
    condition: Condition
    scenario: int | str
    deal_possible: int
    status: Status
    price: int | None
    correct: int
    violation: int
    attempted_violations: int
    refused_calls: int
    turns: int
    tool_calls: int
    turn: Role
    recovered_refusals: int


class EventRecord(FrozenModel):
    """One append-only policy or state-transition record."""

    sequence: int
    negotiation_id: NegotiationId
    event: EventType
    role: Role | None
    action: Action | None
    price: int | None
    reason: str | None
    turn_index: int


class EventInput(FrozenModel):
    """Fields supplied when appending one evidence event."""

    event: EventType
    role: Role | None
    action: Action | None
    price: int | None = None
    reason: str | None = None
