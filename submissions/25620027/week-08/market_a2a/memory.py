"""Persistent own-trade observations keyed by stable shop names."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import Field

from market_a2a.context_snapshot import publish_context
from market_a2a.memory_models import Review, ShopKnowledge, Trade
from market_a2a.memory_retention import MAX_FILE_BYTES, MAX_LOAD_BYTES, retained
from market_a2a.models import FrozenModel, HarnessError

__all__ = ["Memory", "Review", "Trade"]


class Memory(FrozenModel):
    """File boundary for own experience, independent of changing URLs."""

    shops: dict[str, tuple[Trade, ...]] = Field(default_factory=dict)
    profiles: dict[str, ShopKnowledge] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Memory:
        """Reject corrupt existing memory rather than silently erasing it."""
        if not path.exists():
            return cls()
        if path.stat().st_size > MAX_LOAD_BYTES:
            message = "Legacy decision memory exceeds the local migration bound"
            raise HarnessError(message)
        parsed = cls.model_validate_json(path.read_text(encoding="utf-8"))
        return parsed.model_copy(
            update={"shops": retained(parsed.shops, datetime.now(UTC))}
        )

    def save(self, path: Path) -> None:
        """Replace a complete file atomically; retain old data on write failure."""
        path.parent.mkdir(parents=True, exist_ok=True)
        bounded = self.model_copy(
            update={"shops": retained(self.shops, datetime.now(UTC))}
        )
        content = bounded.model_dump_json()
        if len(content.encode()) > MAX_FILE_BYTES:
            message = "Decision memory exceeds its storage bound"
            raise HarnessError(message)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                _ = stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        try:
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        publish_context(bounded.shops, bounded.profiles, path)

    def append(self, name: str, trade: Trade) -> Memory:
        """Create the next snapshot without modifying earlier observations."""
        previous = self.profiles.get(name)
        profile = ShopKnowledge(
            observations=(previous.observations if previous else 0) + 1,
            negotiated_completions=(previous.negotiated_completions if previous else 0)
            + int(trade.purchase.completed),
            last_seen=trade.observed_at,
            last_item=trade.item,
            last_observed_price=trade.purchase.price,
        )
        return Memory(
            shops={**self.shops, name: (*self.shops.get(name, ()), trade)},
            profiles={**self.profiles, name: profile},
        )
