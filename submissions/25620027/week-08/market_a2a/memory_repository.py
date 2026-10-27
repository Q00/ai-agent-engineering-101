"""Choose file persistence or a fast journal without slowing negotiation on export."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from market_a2a.memory import Memory
from market_a2a.memory_journal import MemoryJournal
from market_a2a.memory_retention import retained
from market_a2a.models import HarnessError

if TYPE_CHECKING:
    from market_a2a.memory_models import Trade

LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryRepository:
    """One buyer writes small events; a separate recorder exports context views."""

    snapshot_path: Path
    journal: MemoryJournal | None = None

    @classmethod
    def configured(cls, snapshot: Path) -> MemoryRepository:
        """Use an explicit private journal path only in the prepared VPS runtime."""
        path = os.environ.get("AX_MEMORY_JOURNAL")
        return cls(snapshot, MemoryJournal(Path(path)) if path else None)

    def load(self) -> Memory:
        """Read current events and per-shop notebooks, even if export is behind."""
        if self.journal is not None:
            snapshot = self.journal.snapshot()[1]
            return snapshot.model_copy(
                update={"shops": retained(snapshot.shops, datetime.now(UTC))}
            )
        return Memory.load(self.snapshot_path)

    def record(self, name: str, trade: Trade) -> None:
        """Commit an observation without rendering or compacting context."""
        try:
            if self.journal is not None:
                self.journal.record(name, trade)
            else:
                self.load().append(name, trade).save(self.snapshot_path)
        except (OSError, sqlite3.Error, HarnessError) as exc:
            LOGGER.warning(
                "memory_write_failed kind=%s; retain transaction logs",
                type(exc).__name__,
            )

    def review(self, name: str, trade: Trade) -> None:
        """Update the original event rather than counting a review as a new trade."""
        try:
            if self.journal is not None:
                self.journal.review(trade)
            else:
                memory = self.load()
                records = memory.shops.get(name, ())
                shops = {
                    **memory.shops,
                    name: tuple(
                        trade if record.event_id == trade.event_id else record
                        for record in records
                    ),
                }
                memory.model_copy(update={"shops": shops}).save(self.snapshot_path)
        except (OSError, sqlite3.Error, HarnessError) as exc:
            LOGGER.warning(
                "memory_review_failed kind=%s; retain transaction logs",
                type(exc).__name__,
            )
