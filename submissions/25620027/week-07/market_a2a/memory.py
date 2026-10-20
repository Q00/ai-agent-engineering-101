"""Persistent own-trade observations keyed by stable shop names."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import Field

from market_a2a.models import FrozenModel, Purchase


class Review(FrozenModel):
    """A model-authored evaluation of the observed transaction."""

    stars: int = Field(strict=True, ge=1, le=5)
    review: str = Field(min_length=1)


class Trade(FrozenModel):
    """An own observation survives even if review generation later fails."""

    item: str
    budget: int
    selection_reason: str
    purchase: Purchase
    evaluation: Review | None = None


class Memory(FrozenModel):
    """File boundary for own experience, independent of changing URLs."""

    shops: dict[str, tuple[Trade, ...]] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Memory:
        """Reject corrupt existing memory rather than silently erasing it."""
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        """Replace a complete file atomically; retain old data on write failure."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                _ = stream.write(self.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        try:
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def append(self, name: str, trade: Trade) -> Memory:
        """Create the next snapshot without modifying earlier observations."""
        return Memory(shops={**self.shops, name: (*self.shops.get(name, ()), trade)})
