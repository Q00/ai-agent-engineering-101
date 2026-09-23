"""Typed domain values for the negotiation experiment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    """Reject unknown boundary data and keep parsed values immutable."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


@unique
class Condition(StrEnum):
    """The only experimental intervention."""

    FREE = "free"
    TAGGED = "tagged"
    STRUCTURED = "structured"


@unique
class Performative(StrEnum):
    """The four communicative acts allowed by the assignment."""

    PROPOSE = "propose"
    ACCEPT = "accept-proposal"
    REJECT = "reject-proposal"
    REFUSE = "refuse"


@unique
class Outcome(StrEnum):
    """How an episode terminates."""

    DEAL = "deal"
    NO_DEAL = "no_deal"
    OPEN = "open"


@unique
class Negotiator(StrEnum):
    """The two private-context participants."""

    BUYER = "buyer"
    SELLER = "seller"


@unique
class ChatRole(StrEnum):
    """Roles visible inside one participant's model history."""

    USER = "user"
    ASSISTANT = "assistant"


@unique
class ModelError(StrEnum):
    """Transport failures retained as experiment evidence."""

    TIMEOUT = "timeout"
    CONNECTION = "connection_error"
    API = "api_error"
    EMPTY = "empty_response"


NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]


class Scenario(FrozenModel):
    """A preregistered pair of private bargaining limits."""

    id: int | str
    item: Annotated[str, Field(min_length=1)]
    reserve: NonNegativeInt
    budget: NonNegativeInt

    @property
    def deal_possible(self) -> bool:
        """Return whether the private limits overlap."""
        return self.reserve <= self.budget


class Settings(FrozenModel):
    """Frozen model and run settings shared by all conditions."""

    provider: Annotated[str, Field(min_length=1)]
    base_url: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    temperature: Annotated[float, Field(ge=0, le=2)]
    max_tokens: PositiveInt
    request_timeout_s: Annotated[float, Field(gt=0, le=120)]
    max_turns: PositiveInt
    repetitions: Annotated[int, Field(ge=3)]
    retry_delays_s: tuple[Annotated[float, Field(gt=0)], ...]
    prompt_version: Annotated[str, Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message as seen within a participant or reader history."""

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ModelSuccess:
    """A preserved successful model response."""

    text: str
    raw: str
    tokens: int
    attempts: int


@dataclass(frozen=True, slots=True)
class ModelFailure:
    """A preserved model-call failure."""

    code: ModelError
    detail: str
    attempts: int
    retryable: bool
    stop_batch: bool


type ModelReply = ModelSuccess | ModelFailure


class ModelClient(Protocol):
    """Narrow model seam shared by the live adapter and offline tests."""

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelReply:
        """Return exactly one completion or a typed failure."""
        ...


@dataclass(frozen=True, slots=True)
class EpisodeContext:
    """Inputs that stay fixed for one episode."""

    scenario: Scenario
    condition: Condition
    max_turns: int


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    """The exact episode fields required by results.csv."""

    outcome: Outcome | None
    price: int | None
    correct: int | None
    violation: int | None
    turns: int | None
    format_errors: int | None
    reader_calls: int | None
    note: str


@dataclass(frozen=True, slots=True)
class EpisodeExecution:
    """A result plus the console evidence used to explain it."""

    result: EpisodeResult
    transcript: tuple[str, ...]
    stop_batch: bool = False


@dataclass(frozen=True, slots=True)
class ResultRecord:
    """One append-only results.csv row."""

    run: int
    condition: Condition
    scenario: str
    deal_possible: int
    result: EpisodeResult


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One scenario transcript routed to a condition-and-repeat log."""

    run: int
    condition: Condition
    scenario: str
    execution: EpisodeExecution
    settings: Settings
