"""Append-only run evidence and a recoverable official results table."""

from __future__ import annotations

import csv
import hashlib
import json
from typing import TYPE_CHECKING, Final

from cnp.domain import Condition, FrozenModel
from cnp.evaluate import HEADER, ResultRow
from cnp.settings import InputError, Settings

if TYPE_CHECKING:
    from pathlib import Path

CONDITIONS: Final[tuple[Condition, ...]] = ("baseline", "homogeneous", "overconfident")


class RunMeta(FrozenModel):
    """Reserved identity written before any model calls."""

    run: int
    condition: Condition


def prepare(root: Path, output: Path, settings: Settings) -> None:
    """Refuse to mix edited code/tasks/settings into an existing experiment."""
    output.mkdir(parents=True, exist_ok=True)
    files = [
        root / "tasks.json",
        root / "config.json",
        root / "run.py",
        root / "uv.lock",
        *sorted((root / "cnp").glob("*.py")),
    ]
    signatures = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    manifest = (
        json.dumps(
            {"settings": settings.model_dump(), "sha256": signatures},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    path = output / "experiment.json"
    if path.exists():
        if path.read_text(encoding="utf-8") != manifest:
            msg = "experiment_changed_choose_new_output"
            raise InputError(msg)
    else:
        with path.open("x", encoding="utf-8") as f:
            f.write(manifest)
    (output / "runs").mkdir(exist_ok=True)
    (output / "logs").mkdir(exist_ok=True)


def rows(output: Path) -> tuple[ResultRow, ...]:
    """Recover an interrupted run as crashed without touching its log."""
    results: list[ResultRow] = []
    for directory in sorted((output / "runs").iterdir()):
        meta = RunMeta.model_validate_json((directory / "meta.json").read_bytes())
        result_file = directory / "result.json"
        if not result_file.exists():
            result = ResultRow(
                run=meta.run,
                condition=meta.condition,
                note="crashed: previous process interrupted; original evidence retained",
            )
            with result_file.open("x", encoding="utf-8") as f:
                f.write(result.model_dump_json(indent=2))
        results.append(ResultRow.model_validate_json(result_file.read_bytes()))
    return tuple(results)


def export_csv(output: Path, results: tuple[ResultRow, ...]) -> None:
    """Rebuild CSV from immutable per-run results with atomic replacement."""
    temporary = output / "results.csv.tmp"
    with temporary.open("w", encoding="utf-8", newline="") as f:
        writer: csv.DictWriter[str] = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(row.model_dump() for row in results)
    temporary.replace(output / "results.csv")


def pending(results: tuple[ResultRow, ...], repetitions: int) -> tuple[Condition, ...]:
    """Keep failed attempts; schedule only missing completed repetitions."""
    completed = {
        c: sum(r.condition == c and r.tasks is not None for r in results) for c in CONDITIONS
    }
    return tuple(
        c for repetition in range(repetitions) for c in CONDITIONS if completed[c] <= repetition
    )
