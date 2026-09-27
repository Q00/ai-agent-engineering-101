from __future__ import annotations
from typing import Literal, Optional


from dataclasses import dataclass, field


Performative = Literal["propose", "accept-proposal", "reject-proposal", "refuse"]
PERFORMATIVES: tuple[Performative, ...] = (
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
)
Condition = Literal["free", "tagged", "structured"]
Outcome = Literal["deal", "no_deal", "open"]


@dataclass(frozen=True)
class Scenario:
    id: int
    item: str
    reserve: int
    budget: int

    @property
    def feasible(self) -> bool:
        return self.reserve <= self.budget


@dataclass
class ParsedAct:
    performative: Optional[Performative]
    price: Optional[int]
    format_error: bool
    reader_calls: int = 0
    raw_reader: str = ""


@dataclass
class AclMessage:
    conversation_id: str
    sender: str
    receiver: str
    raw: str
    performative: Optional[Performative] = None
    price: Optional[int] = None
    ontology: str = "used-goods"
    language: str = "en"
    protocol: str = "fipa-propose"
    format_error: bool = False
    reader_calls: int = 0


@dataclass
class EpisodeResult:
    run: str
    condition: str
    scenario: int
    item: str
    deal_posisble: int
    reserve: int
    budget: int
    outcome: Outcome
    price: Optional[int]
    correct: int
    violation: int
    turns: int
    format_errors: int
    reader_calls: int
    input_tokens: int
    output_tokens: int
    note: str = ""
    transcript: list[str] = field(default_factory=list)

    def csv_row(self) -> dict[str, object]:
        return {
            "run": self.run,
            "condition": self.condition,
            "scenario": self.scenario,
            "item": self.item,
            "deal_posisble": self.deal_posisble,
            "reserve": self.reserve,
            "budget": self.budget,
            "outcome": self.outcome,
            "price": "" if self.price is None else self.price,
            "correct": self.correct,
            "violation": self.violation,
            "turns": self.turns,
            "format_errors": self.format_errors,
            "reader_calls": self.reader_calls,
            "note": self.note,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


CSV_FIELDS = [
    "run",
    "condition",
    "scenario",
    "item",
    "deal_posisble",
    "reserve",
    "budget",
    "outcome",
    "price",
    "correct",
    "violation",
    "turns",
    "format_errors",
    "reader_calls",
    "note",
    "input_tokens",
    "output_tokens",
]

