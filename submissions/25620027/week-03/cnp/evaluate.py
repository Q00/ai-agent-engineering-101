"""Offline gold comparison and the exact official CSV row contract."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Final

from cnp.domain import Condition, FrozenModel, Task

if TYPE_CHECKING:
    from cnp.records import Contract, Event

HEADER: Final = (
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
)


class Metrics(FrozenModel):
    """Counts for a completed allocation experiment."""

    tasks: int
    correct: int
    messages: int
    unassigned: int
    misawards: int
    note: str


class ResultRow(FrozenModel):
    """Blank counts signify a crashed run, not a zero-quality success."""

    run: int
    condition: Condition
    tasks: int | None = None
    correct: int | None = None
    messages: int | None = None
    unassigned: int | None = None
    misawards: int | None = None
    note: str = ""


class EvaluationError(Exception):
    """An incomplete allocation cannot be presented as a completed run."""

    def __init__(self, code: str) -> None:
        self.code: str = code
        super().__init__(code)


def evaluate(
    contracts: tuple[Contract, ...], tasks: tuple[Task, ...], events: tuple[Event, ...]
) -> Metrics:
    """Score completed allocations, using only the protocol stream for messages."""
    gold = {t.id: t.gold for t in tasks}
    if len(contracts) != len(gold) or {c.task.id for c in contracts} != set(gold):
        msg = "incomplete_task_set"
        raise EvaluationError(msg)
    if any(c.phase != "CLOSED" for c in contracts):
        msg = "unfinished_contract"
        raise EvaluationError(msg)
    correct = sum(c.winner == gold[c.task.id] for c in contracts)
    unassigned = sum(c.winner is None for c in contracts)
    statuses = Counter(r.status for c in contracts for r in c.responses)
    return Metrics(
        tasks=len(contracts),
        correct=correct,
        unassigned=unassigned,
        misawards=len(contracts) - correct - unassigned,
        messages=sum(e.stream == "protocol_messages" for e in events),
        note="; ".join(f"{s}={statuses[s]}" for s in ("parse_fail", "api_error", "timeout")),
    )
