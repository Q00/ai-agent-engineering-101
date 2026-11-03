"""Typed experiment settings, wire responses, and the official CSV contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Literal, TypedDict, override

from pydantic import BaseModel, ConfigDict, Field


class Shop(StrEnum):
    """Evaluator labels; never shown to the buyer."""

    CHEAP = "cheap"
    PRICEY = "pricey"


class Condition(StrEnum):
    """The only experimental manipulation."""

    PLAIN = "plain"
    INFLATED = "inflated"


class Outcome(StrEnum):
    """Observed terminal outcomes, rather than buyer self-reports."""

    DEAL = "deal"
    REJECTED = "rejected"
    NO_DEAL = "no_deal"
    NO_CHOICE = "no_choice"


class Scenario(BaseModel):
    """A precommitted item, budget, and evaluator-only reserve prices."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    id: str
    item: Literal["lamp", "chair", "bookshelf"]
    budget: int = Field(gt=0)
    cheap_reserve: int = Field(gt=0)
    pricey_reserve: int = Field(gt=0)


class Config(BaseModel):
    """Settings held fixed across both card conditions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    model: str
    temperature: float
    max_turns: int = Field(gt=0)
    repetitions: int = Field(ge=3)
    card_order_seed: int
    low_offer_limit: int = Field(gt=0)
    opening_multiplier: float = Field(gt=1)
    base_url: str
    cheap_name: str
    pricey_name: str
    cheap_port: int
    pricey_port: int


class Receipt(BaseModel):
    """Structured deal artifact, supplied by the seller."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    seller: str
    item: str
    price: int


class Part(BaseModel):
    """Only the fields this buyer needs from a v1.0 Part."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    text: str | None = None
    data: Receipt | None = None


class WireMessage(BaseModel):
    """A visible A2A message."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    parts: tuple[Part, ...] = ()


TaskState = Literal[
    "TASK_STATE_SUBMITTED",
    "TASK_STATE_WORKING",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_AUTH_REQUIRED",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
]


class Status(BaseModel):
    """Externally observable A2A task state."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    state: TaskState
    message: WireMessage | None = None


class Artifact(BaseModel):
    """An A2A result artifact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    parts: tuple[Part, ...] = ()


class Task(BaseModel):
    """The task identity, state, and artifacts needed for a purchase."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    id: str
    context_id: str = Field(alias="contextId")
    status: Status
    artifacts: tuple[Artifact, ...] = ()


class RpcResult(BaseModel):
    """SendMessage wraps its task; GetTask may be parsed independently."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    task: Task | None = None


class RpcError(BaseModel):
    """A JSON-RPC error from the actual server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    code: int
    message: str


class RpcResponse(BaseModel):
    """A response is parsed at the wire boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    result: RpcResult | None = None
    error: RpcError | None = None


@dataclass(frozen=True, slots=True)
class ExperimentError(Exception):
    """A failure whose source and detail can be preserved without secrets."""

    source: str
    detail: str

    @override
    def __str__(self) -> str:
        """Render the typed error source and its detail for the run log."""
        return f"{self.source}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Card:
    """The exact card served, plus its neutral identity and endpoint."""

    name: str
    raw: str
    url: str


class CsvRecord(TypedDict):
    """Exact official CSV row, including blank fields on crashes."""

    run: str
    condition: str
    scenario: str
    item: str
    budget: str
    card_order: str
    chosen: str
    outcome: str
    price: str
    violation: str
    attempted_violations: str
    turns: str
    llm_calls: str
    note: str


HEADER: Final = tuple(CsvRecord.__annotations__)


@dataclass(frozen=True, slots=True)
class Result:
    """One observed episode with no silent price or outcome correction."""

    run: int
    condition: Condition
    scenario: Scenario
    card_order: str
    chosen: Shop | None
    outcome: Outcome | None
    price: int | None
    violation: int
    attempted_violations: int
    turns: int
    llm_calls: int
    note: str

    def csv_record(self) -> CsvRecord:
        """Convert typed values to the required integer-or-blank wire format."""
        return {
            "run": str(self.run),
            "condition": self.condition.value,
            "scenario": self.scenario.id,
            "item": self.scenario.item,
            "budget": str(self.scenario.budget),
            "card_order": self.card_order,
            "chosen": self.chosen.value if self.chosen is not None else "",
            "outcome": self.outcome.value if self.outcome is not None else "",
            "price": str(self.price) if self.price is not None else "",
            "violation": str(self.violation),
            "attempted_violations": str(self.attempted_violations),
            "turns": str(self.turns),
            "llm_calls": str(self.llm_calls),
            "note": self.note,
        }
