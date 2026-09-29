"""Resume-safe Week 05 CSV, console-log, and extension evidence archive."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Final

from market.models import Condition, EpisodeSummary, EventRecord, FrozenModel, Status

if TYPE_CHECKING:
    from pathlib import Path

HEADER: Final = (
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "price",
    "correct",
    "violation",
    "attempted_violations",
    "refused_calls",
    "turns",
    "tool_calls",
    "note",
)


class EpisodeKey(FrozenModel):
    """Unique resume identity for one episode row."""

    run: str
    condition: Condition
    scenario: str


class ResultRow(FrozenModel):
    """Exact grader-facing result schema."""

    run: str
    condition: Condition
    scenario: str
    deal_possible: int | None
    outcome: Status | None
    price: int | None
    correct: int | None
    violation: int | None
    attempted_violations: int | None
    refused_calls: int | None
    turns: int | None
    tool_calls: int | None
    note: str

    def csv_values(self) -> tuple[str, ...]:
        """Serialize blanks exactly as required for crashed episodes."""
        values = (
            self.run,
            self.condition.value,
            self.scenario,
            self.deal_possible,
            self.outcome.value if self.outcome is not None else None,
            self.price,
            self.correct,
            self.violation,
            self.attempted_violations,
            self.refused_calls,
            self.turns,
            self.tool_calls,
            self.note,
        )
        return tuple("" if value is None else str(value) for value in values)

    @classmethod
    def from_summary(
        cls,
        run: str,
        summary: EpisodeSummary,
        note: str,
    ) -> ResultRow:
        """Create a complete row from deterministic server metrics."""
        return cls(
            run=run,
            condition=summary.condition,
            scenario=str(summary.scenario),
            deal_possible=summary.deal_possible,
            outcome=summary.status,
            price=summary.price,
            correct=summary.correct,
            violation=summary.violation,
            attempted_violations=summary.attempted_violations,
            refused_calls=summary.refused_calls,
            turns=summary.turns,
            tool_calls=summary.tool_calls,
            note=note,
        )


class ArchivedEvent(FrozenModel):
    """Extension event joined to its public experiment identity."""

    run: str
    condition: Condition
    scenario: str
    record: EventRecord


class ResultArchive:
    """Append each completed episode once and preserve raw console evidence."""

    def __init__(self, root: Path) -> None:
        self._root: Path = root
        self._results: Path = root / "results.csv"
        self._events: Path = root / "extension" / "shadow_events.jsonl"
        self._trace: Path = root / "extension" / "injection_trace.csv"
        (root / "logs").mkdir(parents=True, exist_ok=True)
        self._events.parent.mkdir(parents=True, exist_ok=True)
        if not self._results.exists():
            with self._results.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(HEADER)
        if not self._trace.exists():
            with self._trace.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(
                    (
                        "run",
                        "condition",
                        "scenario",
                        "event",
                        "role",
                        "action",
                        "price",
                        "reason",
                        "turn_index",
                    )
                )
        self._completed: set[EpisodeKey] = self._read_completed()

    def contains(self, key: EpisodeKey) -> bool:
        """Return whether a row already makes this episode resumable."""
        return key in self._completed

    def append(
        self,
        row: ResultRow,
        events: tuple[EventRecord, ...],
        lines: tuple[str, ...],
    ) -> None:
        """Persist one result, its event ledger, and its condition-run console log."""
        with self._results.open("a", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(row.csv_values())
        with self._events.open("a", encoding="utf-8") as handle:
            for event in events:
                archived = ArchivedEvent(
                    run=row.run,
                    condition=row.condition,
                    scenario=row.scenario,
                    record=event,
                )
                handle.write(f"{archived.model_dump_json()}\n")
        with self._trace.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            for event in events:
                writer.writerow(
                    (
                        row.run,
                        row.condition.value,
                        row.scenario,
                        event.event.value,
                        event.role.value if event.role is not None else "",
                        event.action.value if event.action is not None else "",
                        "" if event.price is None else event.price,
                        event.reason or "",
                        event.turn_index,
                    )
                )
        log_path = self._root / "logs" / f"{row.run}.txt"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
            handle.write("\n")
        self._completed.add(EpisodeKey(run=row.run, condition=row.condition, scenario=row.scenario))

    def _read_completed(self) -> set[EpisodeKey]:
        completed: set[EpisodeKey] = set()
        with self._results.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                condition = row.get("condition", "")
                try:
                    parsed = Condition(condition)
                except ValueError:
                    continue
                else:
                    completed.add(
                        EpisodeKey(
                            run=row.get("run", ""),
                            condition=parsed,
                            scenario=row.get("scenario", ""),
                        )
                    )
        return completed
