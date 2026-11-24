#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic==2.13.5"]
# ///
# ─── How to run ───
# Install uv, then: uv run --frozen python collect_results.py
# ──────────────────
"""Collect finished attempts into the submission CSV without altering raw runs."""

from __future__ import annotations

import csv
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from cnp.evaluate import HEADER, ResultRow


@dataclass(frozen=True, slots=True)
class SubmissionRow:
    """Namespace makes local run IDs unambiguous across model experiments."""

    experiment: str
    result: ResultRow
    log: Path

    @property
    def identifier(self) -> str:
        """Return the submission's stable experiment/run identity."""
        return f"{self.experiment}:{self.result.run}"


def collect(root: Path) -> tuple[SubmissionRow, ...]:
    """Include crashed attempts; skip a still-running attempt without result.json."""
    result: list[SubmissionRow] = []
    sources = {
        "glm-free": root,
        "nemotron-free": root / "experiments" / "nemotron-free",
        "gpt41-nano-paid": root / "experiments" / "gpt41-nano-paid",
    }
    for name, folder in sources.items():
        for path in sorted((folder / "runs").glob("*/result.json")):
            row = ResultRow.model_validate_json(path.read_bytes())
            log = folder / "logs" / f"run-{row.run:03d}-{row.condition}.txt"
            result.append(SubmissionRow(name, row, log))
    return tuple(result)


class EvidenceConflictError(Exception):
    """Refuse to replace a previously preserved console capture."""

    def __init__(self, filename: str) -> None:
        self.filename: str = filename
        super().__init__(filename)


def export(root: Path, rows: tuple[SubmissionRow, ...]) -> None:
    """Write the official table and byte-preserving copies of completed logs."""
    temporary = root / "results.csv.tmp"
    with temporary.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for entry in rows:
            row = entry.result
            target = root / "logs" / f"{entry.experiment}-run-{row.run:03d}-{row.condition}.txt"
            if target.exists():
                if (
                    hashlib.sha256(target.read_bytes()).digest()
                    != hashlib.sha256(entry.log.read_bytes()).digest()
                ):
                    raise EvidenceConflictError(target.name)
            else:
                shutil.copyfile(entry.log, target)
            writer.writerow(
                (
                    entry.identifier,
                    row.condition,
                    row.tasks,
                    row.correct,
                    row.messages,
                    row.unassigned,
                    row.misawards,
                    row.note,
                )
            )
    temporary.replace(root / "results.csv")


if __name__ == "__main__":
    destination = Path(__file__).resolve().parent
    export(destination, collect(destination))
