"""Append-only CSV and console-log evidence with exact resume keys."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Final, final

from acl_lab.domain import Condition, LogRecord, ResultRecord

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
    "turns",
    "format_errors",
    "reader_calls",
    "note",
)


@final
class ResultArchive:
    """Own mutable append-only experiment files and resume state."""

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self.logs: Path = root / "logs"
        self.results_path: Path = root / "results.csv"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        if not self.results_path.exists():
            with self.results_path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow(HEADER)

    def completed_keys(self) -> frozenset[tuple[int, Condition, str]]:
        """Return only rows already persisted under the exact composite key."""
        with self.results_path.open(encoding="utf-8", newline="") as stream:
            rows = csv.DictReader(stream)
            return frozenset(
                (int(row["run"]), Condition(row["condition"]), row["scenario"]) for row in rows
            )

    def append(self, record: ResultRecord) -> None:
        """Append one episode immediately so an interrupted batch can resume."""
        result = record.result
        with self.results_path.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(
                (
                    record.run,
                    record.condition.value,
                    record.scenario,
                    record.deal_possible,
                    result.outcome.value if result.outcome is not None else "",
                    result.price if result.price is not None else "",
                    result.correct if result.correct is not None else "",
                    result.violation if result.violation is not None else "",
                    result.turns if result.turns is not None else "",
                    result.format_errors if result.format_errors is not None else "",
                    result.reader_calls if result.reader_calls is not None else "",
                    result.note,
                )
            )

    def append_log(self, record: LogRecord) -> None:
        """Append one scenario transcript to its condition-and-repeat capture."""
        path = self.logs / f"{record.condition.value}-{record.run:02d}.txt"
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as stream:
            if is_new:
                metadata = (
                    f"provider={record.settings.provider} model={record.settings.model} "
                    f"temperature={record.settings.temperature} "
                    f"max_turns={record.settings.max_turns}\n"
                )
                stream.write(metadata)
            stream.write(f"\n=== scenario {record.scenario} ===\n")
            stream.write("\n".join(record.execution.transcript))
            stream.write("\n")
