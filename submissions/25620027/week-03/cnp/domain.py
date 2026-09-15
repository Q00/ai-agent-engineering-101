"""Typed inputs and pure allocation rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

Agent = Literal["A", "B", "C"]
Condition = Literal["baseline", "homogeneous", "overconfident"]
TaskId = NewType("TaskId", str)
ContractId = NewType("ContractId", str)
Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class FrozenModel(BaseModel):
    """Strict boundary value that cannot mutate after parsing."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Task(FrozenModel):
    """Committed task plus evaluator-only gold."""

    id: TaskId
    desc: Text
    gold: Agent


class RuntimeTask(FrozenModel):
    """Only these task fields may reach a contractor."""

    id: TaskId
    desc: Text


class Bid(FrozenModel):
    """The only three fields an LLM may return."""

    bid: StrictBool
    confidence: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)]
    reason: Text


@dataclass(frozen=True, slots=True)
class Candidate:
    """Harness-authenticated bidder plus parsed model output."""

    agent: Agent
    proposal: Bid


def parse_bid(raw: str) -> Bid:
    """Parse one complete JSON object, without extraction or repair."""
    return Bid.model_validate_json(raw)


def select_winner(candidates: tuple[Candidate, ...]) -> Agent | None:
    """Choose the first highest-confidence true bid in call order."""
    eligible = tuple(c for c in candidates if c.proposal.bid)
    if not eligible:
        return None
    return max(eligible, key=lambda c: c.proposal.confidence).agent


def runtime_task(task: Task) -> RuntimeTask:
    """Project a committed task without exposing its gold label."""
    return RuntimeTask(id=task.id, desc=task.desc)
