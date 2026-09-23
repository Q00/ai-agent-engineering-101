"""Append-only evidence storage for the hybrid extension."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from pathlib import Path

    from acl_lab.hybrid_domain import HybridLogRecord, HybridResultRecord

HYBRID_HEADER: Final = (
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
    "settlement_calls",
    "settlement_errors",
    "settlement_vetoes",
    "guard_vetoes",
    "note",
)


@final
class HybridArchive:
    """Own the hybrid extension's separate mutable evidence files."""

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self.logs: Path = root / "logs"
        self.results_path: Path = root / "hybrid_results.csv"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        if not self.results_path.exists():
            with self.results_path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow(HYBRID_HEADER)

    def completed_keys(self) -> frozenset[tuple[int, str]]:
        """Return exact persisted `(run, scenario)` keys."""
        with self.results_path.open(encoding="utf-8", newline="") as stream:
            rows = csv.DictReader(stream)
            return frozenset((int(row["run"]), row["scenario"]) for row in rows)

    def append(self, record: HybridResultRecord) -> None:
        """Append one hybrid episode immediately for safe resume."""
        result = record.result
        with self.results_path.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(
                (
                    record.run,
                    "hybrid",
                    record.scenario,
                    record.deal_possible,
                    result.outcome.value if result.outcome is not None else "",
                    result.price if result.price is not None else "",
                    result.correct if result.correct is not None else "",
                    result.violation if result.violation is not None else "",
                    result.turns if result.turns is not None else "",
                    result.format_errors if result.format_errors is not None else "",
                    result.reader_calls if result.reader_calls is not None else "",
                    result.settlement_calls,
                    result.settlement_errors,
                    result.settlement_vetoes,
                    result.guard_vetoes,
                    result.note,
                )
            )

    def append_log(self, record: HybridLogRecord) -> None:
        """Append one scenario transcript to its repeat log."""
        path = self.logs / f"hybrid-{record.run:02d}.txt"
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as stream:
            if is_new:
                provider = f"provider={record.settings.provider} model={record.settings.model}"
                temperature = f"temperature={record.settings.temperature}"
                turns = f"max_turns={record.settings.max_turns}"
                metadata = f"{provider} {temperature} {turns}\n"
                stream.write(metadata)
            stream.write(f"\n=== scenario {record.scenario} ===\n")
            stream.write("\n".join(record.execution.transcript))
            stream.write("\n")
