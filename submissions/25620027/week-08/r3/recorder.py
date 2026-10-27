"""A non-LLM worker publishes context while buyer and seller continue trading."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Annotated, Final

import anyio
import typer

from market_a2a.memory_journal import MemoryJournal
from market_a2a.models import HarnessError

LOGGER: Final = logging.getLogger(__name__)


async def record_loop(journal: Path, snapshot: Path) -> None:
    """Separate process; snapshots may lag, but buyers read the committed journal."""
    store = await anyio.to_thread.run_sync(MemoryJournal, journal)
    while True:
        try:
            changed = await anyio.to_thread.run_sync(store.compact, snapshot)
            if changed:
                typer.echo("RECORDER_EXPORTED")
        except (OSError, sqlite3.Error, HarnessError) as exc:
            LOGGER.warning("recorder_export_failed kind=%s", type(exc).__name__)
        await anyio.sleep(1)


def main(
    journal: Annotated[Path, typer.Option()],
    snapshot: Annotated[Path, typer.Option()],
    *,
    once: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run local recording only; this executable has no market or model client."""
    journal_path = Path(journal).resolve()
    snapshot_path = Path(snapshot).resolve()
    if once:
        _ = MemoryJournal(journal_path).compact(snapshot_path)
    else:
        anyio.run(record_loop, journal_path, snapshot_path)


if __name__ == "__main__":
    typer.run(main)
