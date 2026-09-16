from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from collect_results import collect, export

if TYPE_CHECKING:
    from pathlib import Path


def test_collection_retains_failed_attempt_and_skips_in_progress(tmp_path: Path) -> None:
    # Given one historic failure, one completed alternative run and one live run.
    old = tmp_path / "runs" / "run-001"
    new = tmp_path / "experiments" / "nemotron-free" / "runs" / "run-001"
    for p in (old, new, new.parent / "run-002"):
        p.mkdir(parents=True)
    (old / "result.json").write_text('{"run":1,"condition":"baseline","note":"crashed: HTTP 429"}')
    (new / "result.json").write_text(
        '{"run":1,"condition":"baseline","tasks":6,"correct":4,"messages":36,"unassigned":0,"misawards":2}'
    )
    for root in (tmp_path, new.parent.parent):
        (root / "logs").mkdir()
        (root / "logs" / "run-001-baseline.txt").write_text("unchanged original trace\n")
    # When collecting finished records.
    records = collect(tmp_path)
    export(tmp_path, records)
    # Then original IDs stay namespaced, failed counts stay blank, live run is absent.
    with (tmp_path / "results.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [(r["run"], r["tasks"]) for r in rows] == [("glm-free:1", ""), ("nemotron-free:1", "6")]
    assert (
        tmp_path / "logs" / "nemotron-free-run-001-baseline.txt"
    ).read_text() == "unchanged original trace\n"


def test_collection_keeps_paid_experiment_separate(tmp_path: Path) -> None:
    paid = tmp_path / "experiments" / "gpt41-nano-paid" / "runs" / "run-001"
    paid.mkdir(parents=True)
    (paid / "result.json").write_text(
        '{"run":1,"condition":"baseline","tasks":6,"correct":5,"messages":36,"unassigned":0,"misawards":1}'
    )
    records = collect(tmp_path)
    assert len(records) == 1
    assert records[0].identifier == "gpt41-nano-paid:1"
    assert records[0].result.tasks == 6
