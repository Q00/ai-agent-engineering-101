"""Record100 shops, reload their notebooks and measure the actual producer path."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import TYPE_CHECKING

import anyio
from anyio.streams.text import TextReceiveStream

from market_a2a.memory import Memory, Review
from market_a2a.memory_journal import MemoryJournal
from market_a2a.memory_repository import MemoryRepository
from tests.test_memory_selection import observation

if TYPE_CHECKING:
    import pytest


def test_all100_shop_notebooks_survive_detail_expiry_and_restart(
    tmp_path: Path,
) -> None:
    # Given:100 actually visited identities, including observations older than14 days.
    path, snapshot = tmp_path / "memory.sqlite", tmp_path / "buyer-memory.json"
    journal = MemoryJournal(path)
    for index in range(100):
        trade = observation(f"task-{index}").model_copy(
            update={"observed_at": datetime.now(UTC) - timedelta(days=15)}
        )
        journal.record(f"shop-{index}", trade)
        journal.record(f"shop-{index}", trade)
        journal.review(
            trade.model_copy(update={"evaluation": Review(stars=3, review="Observed.")})
        )
    # When: a recorder compacts expired detail and both processes restart.
    assert journal.compact(snapshot)
    _, current = MemoryJournal(path).snapshot()
    # Then: every shop notebook remains; duplicate writes/reviews do not inflate counts.
    assert len(current.profiles) == 100
    assert not current.shops
    assert all(profile.observations == 1 for profile in current.profiles.values())
    assert len(Memory.load(snapshot).profiles) == 100
    assert snapshot.with_suffix(".context.md").is_file()


def test_producer_does_not_export_context_inline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: inline Markdown/snapshot export would fail immediately if attempted.
    journal = MemoryJournal(tmp_path / "memory.sqlite")
    repository = MemoryRepository(tmp_path / "memory.json", journal)

    def forbid_export(_memory: Memory, _path: Path) -> None:
        message = "Producer cannot render context during trading"
        raise AssertionError(message)

    monkeypatch.setattr(Memory, "save", forbid_export)
    timings: list[float] = []
    # When: commit100 independent observations using the actual trading producer.
    for index in range(100):
        started = perf_counter_ns()
        repository.record(f"shop-{index}", observation(f"task-{index}"))
        timings.append((perf_counter_ns() - started) / 1_000_000)
    # Then: all were stored without export; report latency rather than a universal SLA.
    assert len(repository.load().profiles) == 100
    assert not repository.snapshot_path.exists()
    ordered = sorted(timings)
    print(
        {
            "shops": 100,
            "record_ms_median": median(timings),
            "record_ms_p95": ordered[94],
            "record_ms_max": max(timings),
        }
    )


def test_recorder_runs_in_another_process_while_producer_keeps_writing(
    tmp_path: Path,
) -> None:
    # Given: a genuine recorder subprocess, not a mocked background function.
    source = Path(__file__).resolve().parents[1]

    async def run() -> None:
        database, snapshot = tmp_path / "memory.sqlite", tmp_path / "memory.json"
        journal = MemoryJournal(database)
        async with await anyio.open_process(
            (
                sys.executable,
                "-m",
                "r3.recorder",
                "--journal",
                str(database),
                "--snapshot",
                str(snapshot),
            ),
            cwd=source,
            stderr=None,
        ) as worker:
            try:
                # When: the producer writes while the separate recorder exports.
                for index in range(100):
                    await anyio.to_thread.run_sync(
                        journal.record, f"shop-{index}", observation(f"task-{index}")
                    )
                assert worker.stdout is not None
                with anyio.fail_after(5):
                    async for _chunk in TextReceiveStream(worker.stdout):
                        state = await anyio.to_thread.run_sync(Memory.load, snapshot)
                        if len(state.profiles) == 100:
                            break
                # Then: all shop notebooks are present and both roles remained alive.
                assert worker.returncode is None
                assert len(journal.snapshot()[1].profiles) == 100
            finally:
                worker.terminate()
                _ = await worker.wait()

    anyio.run(run)
